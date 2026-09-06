from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import TemporaryDirectory

from .downloader import InvoiceDownloader
from .verifier import failed_result, verify_file


def run_batch(records, stop_event, emit, download_workers=4):
    """Download into a disposable batch directory, then verify in row order."""
    records = list(records)
    with TemporaryDirectory(prefix="invoice-verifier-") as folder:
        # TemporaryDirectory owns this absolute OS-created directory and cleanup.
        batch_dir = Path(folder).resolve()

        def download(record):
            if stop_event.is_set():
                return None
            # Row-specific names prevent collisions, even for duplicate references.
            downloader = InvoiceDownloader(batch_dir)
            try:
                return downloader.download(record.attachment_url, f"row-{record.excel_row}")
            finally:
                downloader.session.close()

        downloaded = {}
        errors = {}
        emit(("status", f"Downloading {len(records)} invoice(s), up to {download_workers} at a time..."))
        with ThreadPoolExecutor(max_workers=download_workers) as pool:
            futures = {pool.submit(download, record): record for record in records}
            for count, future in enumerate(as_completed(futures), 1):
                record = futures[future]
                if stop_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
                try:
                    downloaded[record.excel_row] = future.result()
                except Exception as exc:
                    errors[record.excel_row] = str(exc)
                emit(("status", f"Downloaded {count}/{len(records)} invoice(s); {len(errors)} failed"))

        for index, record in enumerate(records, 1):
            if stop_event.is_set():
                break
            emit(("status", f"Verifying {index}/{len(records)}: {record.invoice_number}"))
            path = downloaded.get(record.excel_row)
            try:
                if record.excel_row in errors:
                    result = failed_result(record, f"Download failed: {errors[record.excel_row]}")
                elif path is None:
                    result = failed_result(record, "Invoice download unavailable")
                else:
                    result = verify_file(record, path)
            except Exception as exc:
                result = failed_result(record, str(exc))
            # Results must not retain paths that are deleted after the batch.
            result.downloaded_file = None
            emit(("result", result, index, len(records)))
