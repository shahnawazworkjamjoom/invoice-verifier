from dataclasses import replace
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from invoice_verifier.batch import run_batch
from invoice_verifier.models import VerificationResult
from tests.test_verifier import record


class BatchTests(unittest.TestCase):
    def test_downloads_finish_before_ocr_and_rows_stay_mapped(self):
        records = [replace(record(), excel_row=i) for i in (2, 3, 4)]
        barrier = threading.Barrier(3)
        downloaded, verified, folders, events = [], [], [], []

        def download(downloader, url, reference):
            path = downloader.cache_dir / (reference + '.pdf')
            path.write_text(reference)
            folders.append(path.parent)
            barrier.wait(timeout=5)  # Downloads must actually overlap.
            downloaded.append(reference)
            return path

        def verify(row, path):
            self.assertEqual(3, len(downloaded))
            self.assertEqual(f'row-{row.excel_row}', path.read_text())
            verified.append(row.excel_row)
            return VerificationResult(row, 'APPROVE', '', downloaded_file=path)

        with patch('invoice_verifier.batch.InvoiceDownloader.download', download), patch(
                'invoice_verifier.batch.verify_file', side_effect=verify):
            run_batch(records, threading.Event(), events.append, download_workers=3)
        self.assertEqual([2, 3, 4], verified)
        self.assertTrue(all(not folder.exists() for folder in folders))
        self.assertTrue(all(e[1].downloaded_file is None for e in events if e[0] == 'result'))

    def test_failure_declines_only_its_row_and_cleans_up(self):
        folders, events = [], []
        def download(downloader, url, reference):
            folders.append(downloader.cache_dir)
            if reference == 'row-2':
                raise RuntimeError('network failed')
            path = downloader.cache_dir / reference
            path.write_bytes(b'invoice')
            return path
        with patch('invoice_verifier.batch.InvoiceDownloader.download', download), patch(
                'invoice_verifier.batch.verify_file', side_effect=lambda r, p: VerificationResult(r, 'APPROVE', '')):
            run_batch([record(), replace(record(), excel_row=3)], threading.Event(), events.append)
        results = [e[1] for e in events if e[0] == 'result']
        self.assertEqual(['DECLINE', 'APPROVE'], [r.decision for r in results])
        self.assertTrue(all(not folder.exists() for folder in folders))

    def test_stop_during_download_cleans_up_without_ocr(self):
        stop = threading.Event()
        folders = []
        def download(downloader, url, reference):
            folders.append(downloader.cache_dir)
            path = downloader.cache_dir / reference
            path.write_bytes(b'invoice')
            stop.set()
            return path
        with patch('invoice_verifier.batch.InvoiceDownloader.download', download), patch(
                'invoice_verifier.batch.verify_file') as verify:
            run_batch([record()], stop, lambda e: None)
        verify.assert_not_called()
        self.assertTrue(all(not folder.exists() for folder in folders))

    def test_new_run_downloads_again(self):
        with patch('invoice_verifier.batch.InvoiceDownloader.download', side_effect=RuntimeError('offline')) as download:
            for _ in range(2):
                run_batch([record()], threading.Event(), lambda e: None)
        self.assertEqual(2, download.call_count)
