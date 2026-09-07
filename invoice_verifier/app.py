from __future__ import annotations

import webbrowser
import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import audit
from .batch import run_batch
from .branding import LOGO_PNG_BASE64
from .corrections import export_decision_workbook
from .models import InvoiceRecord, VerificationResult
from .workbook import read_records


APP_DIR = Path(__file__).resolve().parent.parent
REPORT_DIR = APP_DIR / "reports"


class InvoiceVerifierApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("KOJ Invoice Verifier")
        self.geometry("1380x820")
        self.minsize(1050, 650)
        self.records: list[InvoiceRecord] = []
        self.results: dict[int, VerificationResult] = {}
        self.workbook_path: Path | None = None
        self.audit_path: Path | None = None
        self.run_started_at: datetime | None = None
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.logo_image = tk.PhotoImage(data=LOGO_PNG_BASE64)
        self.header_logo = self.logo_image.subsample(3, 3)
        self.iconphoto(True, self.logo_image)
        self._build_style()
        self._build_ui()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.status_label.configure(text="Closing after active work finishes and temporary files are removed...")
            self.after(100, self._close)
            return
        self.destroy()

    def _build_style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Treeview", rowheight=28)
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 10))
        style.configure("Approve.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Decline.TButton", font=("Segoe UI", 10, "bold"))

    def _build_ui(self):
        top = ttk.Frame(self, padding=(14, 12))
        top.pack(fill="x")
        logo = ttk.Label(top, image=self.header_logo)
        logo.pack(side="left", padx=(0, 10))
        ttk.Label(top, text="KOJ Invoice Verifier", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Upload Excel", command=self.choose_workbook).pack(side="left", padx=(20, 6))
        self.verify_all_button = ttk.Button(top, text="Verify all", command=self.verify_all, state="disabled")
        self.verify_all_button.pack(side="left", padx=6)
        self.verify_selected_button = ttk.Button(top, text="Verify selected", command=self.verify_selected, state="disabled")
        self.verify_selected_button.pack(side="left", padx=6)
        self.stop_button = ttk.Button(top, text="Stop", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=6)
        self.export_button = ttk.Button(top, text="Export result", command=self.export_report, state="disabled")
        self.export_button.pack(side="left", padx=6)
        ttk.Button(top, text="History", command=self.open_history).pack(side="left", padx=6)

        file_frame = ttk.Frame(self, padding=(14, 0, 14, 8))
        file_frame.pack(fill="x")
        self.file_label = ttk.Label(file_frame, text="No workbook selected")
        self.file_label.pack(side="left", fill="x", expand=True)
        self.summary_label = ttk.Label(file_frame, text="")
        self.summary_label.pack(side="right")

        table_frame = ttk.Frame(self, padding=(14, 0))
        table_frame.pack(fill="both", expand=True)
        columns = ("row", "supplier", "location", "order", "invoice", "amount", "decision", "remarks")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        headings = {
            "row": "Excel row", "supplier": "Supplier", "location": "Location",
            "order": "Order no.", "invoice": "Invoice no.", "amount": "Amount to pay",
            "decision": "Decision", "remarks": "Remarks",
        }
        widths = {"row": 75, "supplier": 105, "location": 210, "order": 145,
                  "invoice": 170, "amount": 100, "decision": 115, "remarks": 390}
        for key in columns:
            self.tree.heading(key, text=headings[key], command=lambda k=key: self._sort(k, False))
            self.tree.column(key, width=widths[key], minwidth=65, stretch=key in ("location", "remarks"))
        self.tree.tag_configure("APPROVE", background="#e7f5e9")
        self.tree.tag_configure("DECLINE", background="#fde8e7")
        self.tree.bind("<<TreeviewSelect>>", self._show_details)
        self.tree.bind("<Double-1>", self._toggle_decision)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.decision_menu = tk.Menu(self, tearoff=0)
        self.decision_menu.add_command(label="Mark APPROVE", command=lambda: self.override_decision("APPROVE"))
        self.decision_menu.add_command(label="Mark DECLINE", command=lambda: self.override_decision("DECLINE"))
        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        detail_frame = ttk.LabelFrame(self, text="Verification details", padding=8)
        detail_frame.pack(fill="both", padx=14, pady=(8, 4))
        self.details = tk.Text(detail_frame, height=8, wrap="word", font=("Consolas", 9), state="disabled")
        self.details.pack(side="left", fill="both", expand=True)
        detail_buttons = ttk.Frame(detail_frame)
        detail_buttons.pack(side="right", fill="y", padx=(8, 0))
        ttk.Button(detail_buttons, text="Open invoice", command=self.open_invoice).pack(fill="x", pady=2)
        ttk.Button(detail_buttons, text="Copy details", command=self.copy_details).pack(fill="x", pady=2)
        tk.Button(detail_buttons, text="✓  Mark APPROVE",
                  command=lambda: self.override_decision("APPROVE"),
                  bg="#2E7D32", fg="white", activebackground="#1B5E20", activeforeground="white",
                  font=("Segoe UI", 10, "bold"), relief="raised", bd=2,
                  padx=8, pady=4, cursor="hand2").pack(fill="x", pady=(8, 2))
        tk.Button(detail_buttons, text="✕  Mark DECLINE",
                  command=lambda: self.override_decision("DECLINE"),
                  bg="#C62828", fg="white", activebackground="#7F1D1D", activeforeground="white",
                  font=("Segoe UI", 10, "bold"), relief="raised", bd=2,
                  padx=8, pady=4, cursor="hand2").pack(fill="x", pady=2)

        bottom = ttk.Frame(self, padding=(14, 4, 14, 12))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")
        self.status_label = ttk.Label(bottom, text="Ready", style="Status.TLabel")
        self.status_label.pack(anchor="w", pady=(4, 0))

    def choose_workbook(self):
        path = filedialog.askopenfilename(title="Select purchase order workbook",
                                          filetypes=[("Excel workbooks", "*.xlsx"), ("All files", "*.*")])
        if not path:
            return
        try:
            records = read_records(path)
        except Exception as exc:
            messagebox.showerror("Cannot read workbook", str(exc))
            return
        self.workbook_path = Path(path)
        self.records = records
        self.results.clear()
        self.run_started_at = datetime.now()
        self.audit_path = audit.new_audit_path(self.workbook_path, self.run_started_at)
        for item in self.tree.get_children():
            self.tree.delete(item)
        for record in records:
            amount = "" if record.amount_to_pay is None else f"{record.amount_to_pay:,.2f} {record.currency}"
            self.tree.insert("", "end", iid=str(record.excel_row), values=(
                record.excel_row, record.supplier, record.location, record.order_number,
                record.invoice_number, amount, "PENDING", "Not processed"))
        self.file_label.configure(text=str(self.workbook_path))
        self.progress.configure(value=0, maximum=max(len(records), 1))
        self.status_label.configure(
            text=(f"Loaded {len(records)} invoice records. Local OCR ready; NVIDIA Vision on hold. "
                  "Source workbook will not be changed."))
        self._set_ready_state()
        self._update_summary()

    def verify_all(self):
        self._start(self.records)

    def verify_selected(self):
        selected = {int(item) for item in self.tree.selection()}
        if not selected:
            messagebox.showinfo("Select records", "Select one or more rows to verify.")
            return
        self._start([r for r in self.records if r.excel_row in selected])

    def _start(self, records: list[InvoiceRecord]):
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.progress.configure(value=0, maximum=max(len(records), 1))
        self.verify_all_button.configure(state="disabled")
        self.verify_selected_button.configure(state="disabled")
        self.export_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.worker = threading.Thread(target=self._work, args=(records,), daemon=True)
        self.worker.start()

    def _work(self, records: list[InvoiceRecord]):
        try:
            run_batch(records, self.stop_event, self.events.put)
        except Exception as exc:
            self.events.put(("status", f"Batch failed: {exc}"))
        finally:
            self.events.put(("done", self.stop_event.is_set()))

    def stop(self):
        self.stop_event.set()
        self.status_label.configure(text="Stopping; waiting for active downloads or OCR to finish and cleaning temporary files...")

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "status":
                    self.status_label.configure(text=event[1])
                elif event[0] == "result":
                    result, index, total = event[1:]
                    self.results[result.record.excel_row] = result
                    iid = str(result.record.excel_row)
                    values = list(self.tree.item(iid, "values"))
                    values[6], values[7] = result.decision, result.remarks
                    self.tree.item(iid, values=values, tags=(result.decision,))
                    self.progress.configure(value=index, maximum=total)
                    self._update_summary()
                    if len(self.results) % 25 == 0:
                        self._save_audit(quiet=True)
                elif event[0] == "done":
                    self._set_ready_state()
                    word = "stopped" if event[1] else "finished"
                    self._save_audit(quiet=True)
                    if self.workbook_path and self.audit_path and self.results:
                        try:
                            audit.log_history_index(
                                self.audit_path, self.workbook_path,
                                self.results, self.run_started_at)
                        except Exception:
                            pass
                    audit_hint = f" Audit saved: {self.audit_path}" if self.audit_path and self.results else ""
                    self.status_label.configure(
                        text=f"Verification {word}. {len(self.results)} result(s) available.{audit_hint}")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _set_ready_state(self):
        state = "normal" if self.records else "disabled"
        self.verify_all_button.configure(state=state)
        self.verify_selected_button.configure(state=state)
        self.stop_button.configure(state="disabled")
        self.export_button.configure(state="normal" if self.results else "disabled")

    def _update_summary(self):
        counts = {name: 0 for name in ("APPROVE", "DECLINE")}
        for result in self.results.values():
            counts[result.decision] += 1
        pending = len(self.records) - len(self.results)
        self.summary_label.configure(text=(f"Approve {counts['APPROVE']}  |  "
                                           f"Decline {counts['DECLINE']}  |  Pending {pending}"))

    def _show_details(self, _event=None):
        selection = self.tree.selection()
        text = ""
        if selection:
            result = self.results.get(int(selection[0]))
            if result:
                lines = [f"Decision: {result.decision}", f"Remarks: {result.remarks}", ""]
                lines.extend(f"{key}: {value}" for key, value in result.checks.items())
                text = "\n".join(lines)
            else:
                text = "This row has not been processed."
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def override_decision(self, decision: str):
        """Let the user change APPROVE <-> DECLINE; export uses this updated value."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("No rows selected", "Select one or more verified rows first.")
            return
        updated, skipped = 0, 0
        for iid in selection:
            try:
                excel_row = int(iid)
            except (TypeError, ValueError):
                continue
            result = self.results.get(excel_row)
            if result is None:
                skipped += 1
                continue
            if result.decision == decision:
                continue
            original = result.decision
            result.decision = decision
            result.checks["Manual Override"] = f"Changed from {original} to {decision} by user"
            values = list(self.tree.item(iid, "values"))
            if len(values) >= 7:
                values[6] = decision
                self.tree.item(iid, values=values, tags=(decision,))
            updated += 1
        self._update_summary()
        self._show_details()
        if updated:
            self._save_audit(quiet=True)
            self.status_label.configure(
                text=f"Manually set {updated} row(s) to {decision}. Export + audit will use the updated decision.")
        elif skipped:
            messagebox.showinfo("Not verified",
                                "Selected row(s) have not been verified yet. Verify them first.")
        else:
            self.status_label.configure(text=f"Selected row(s) already {decision}.")

    def _toggle_decision(self, event=None):
        """Double-click the Decision cell to flip APPROVE <-> DECLINE."""
        try:
            if event is not None:
                column = self.tree.identify_column(event.x)
                if column != "#7":
                    return
                row_id = self.tree.identify_row(event.y)
                if not row_id:
                    return
                self.tree.selection_set(row_id)
        except Exception:
            return
        selection = self.tree.selection()
        if not selection:
            return
        try:
            result = self.results.get(int(selection[0]))
        except (TypeError, ValueError):
            return
        if result is None:
            return
        new_decision = "DECLINE" if result.decision == "APPROVE" else "APPROVE"
        self.override_decision(new_decision)

    def _show_context_menu(self, event=None):
        try:
            if event is not None:
                row_id = self.tree.identify_row(event.y)
                if row_id and row_id not in self.tree.selection():
                    self.tree.selection_set(row_id)
                self.decision_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.decision_menu.grab_release()
            except Exception:
                pass

    def open_invoice(self):
        selection = self.tree.selection()
        if not selection:
            return
        result = self.results.get(int(selection[0]))
        if not result:
            messagebox.showinfo("Invoice unavailable", "Verify this row first.")
            return
        url = result.record.attachment_url
        if not url.lower().startswith(("http://", "https://")):
            messagebox.showinfo("Invoice unavailable", "This row has no valid invoice URL.")
            return
        webbrowser.open(url)

    def copy_details(self):
        text = self.details.get("1.0", "end-1c")
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def export_report(self):
        if not self.results or not self.workbook_path:
            return
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        default = f"{self.workbook_path.stem}_result_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        path = filedialog.asksaveasfilename(title="Save verification result", initialdir=REPORT_DIR,
                                            initialfile=default, defaultextension=".xlsx",
                                            filetypes=[("Excel workbook", "*.xlsx")])
        if not path:
            return
        try:
            count = export_decision_workbook(self.workbook_path, path, self.results)
            audit.embed_audit_sheet(path, self.workbook_path, self.results)
        except Exception as exc:
            messagebox.showerror("Result export failed", str(exc))
            return
        self._save_audit(quiet=True)
        if self.workbook_path and self.audit_path:
            try:
                audit.log_history_index(
                    self.audit_path, self.workbook_path, self.results, self.run_started_at)
            except Exception:
                pass
        self.status_label.configure(text=f"Result workbook saved: {path} | Audit: {self.audit_path}")
        messagebox.showinfo("Result saved",
                            f"Saved {count} decision(s). Finance Amount To Pay is unchanged "
                            f"in the original Excel structure.\n\nResult:\n{path}\n\n"
                            f"Long-term audit (why each decision):\n{self.audit_path}\n"
                            f"Plus sheet 'Verification_Audit' inside the result workbook.")

    def _save_audit(self, quiet: bool = True):
        """Persist verification details for future lookup (1 year / 10 years)."""
        try:
            if not self.workbook_path or not self.results:
                return
            if self.audit_path is None:
                self.run_started_at = self.run_started_at or datetime.now()
                self.audit_path = audit.new_audit_path(self.workbook_path, self.run_started_at)
            audit.save_audit_snapshot(
                self.audit_path, self.workbook_path, self.results, self.run_started_at)
        except Exception as exc:
            if not quiet:
                messagebox.showwarning("Audit save failed",
                                       f"Verification finished but audit history could not be saved:\n{exc}")
            else:
                self.status_label.configure(text=f"Warning: audit save failed: {exc}")

    def open_history(self):
        """Open the long-term audit folder so old decisions can be found."""
        try:
            audit.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(audit.HISTORY_DIR)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(audit.HISTORY_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(audit.HISTORY_DIR)])
        except Exception as exc:
            messagebox.showinfo("History folder", f"{audit.HISTORY_DIR}\n\nCould not auto-open: {exc}")

    def _sort(self, column: str, reverse: bool):
        items = [(self.tree.set(item, column), item) for item in self.tree.get_children("")]
        if column in ("row", "amount"):
            def key(pair):
                try:
                    return float(str(pair[0]).replace(",", "").split()[0])
                except ValueError:
                    return 0
        else:
            key = lambda pair: pair[0].lower()
        items.sort(key=key, reverse=reverse)
        for index, (_, item) in enumerate(items):
            self.tree.move(item, "", index)
        self.tree.heading(column, command=lambda: self._sort(column, not reverse))


def main():
    try:
        app = InvoiceVerifierApp()
        app.mainloop()
    except Exception:
        traceback.print_exc()
        raise
