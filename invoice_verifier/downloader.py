from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class InvoiceDownloader:
    def __init__(self, cache_dir: str | Path, timeout: int = 45):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(total=3, connect=3, read=3, backoff_factor=0.7,
                      status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=frozenset(("GET", "HEAD")))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers["User-Agent"] = "InvoiceVerifier/1.0"

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:120] or "invoice"

    def download(self, url: str, reference: str) -> Path:
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("Attachment is not an HTTP(S) URL.")
        parsed_name = Path(urlparse(url).path).name
        suffix = Path(parsed_name).suffix.lower()
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        provisional = self.cache_dir / f"{self._safe_name(reference)}_{key}{suffix}"
        if provisional.exists() and provisional.stat().st_size:
            return provisional

        response = self.session.get(url, timeout=self.timeout, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if not suffix:
            suffix = mimetypes.guess_extension(content_type) or ".bin"
            provisional = provisional.with_suffix(suffix)
        allowed = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
        if suffix not in allowed and not (content_type.startswith("image/") or content_type == "application/pdf"):
            raise ValueError(f"Unsupported attachment type: {content_type or suffix or 'unknown'}")
        partial = provisional.with_suffix(provisional.suffix + ".part")
        try:
            with partial.open("wb") as target:
                for chunk in response.iter_content(1024 * 128):
                    if chunk:
                        target.write(chunk)
            if not partial.stat().st_size:
                raise ValueError("Downloaded attachment is empty.")
            partial.replace(provisional)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return provisional
