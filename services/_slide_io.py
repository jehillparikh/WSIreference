"""
services/_slide_io.py
Shared byte-range file access for any slide_url (local:// or gs:///https://),
used by both ThumbnailService and SlideNormalizationService — anything that
needs to open a slide with `tifffile` without downloading the whole file
first.

Mirrors what the browser's GeoTIFFTileSource does (fetch only the byte
ranges tifffile's IFD-walking actually asks for), just server-side.
"""
from __future__ import annotations

import logging
from typing import Optional

from services.gcs_stream_service import is_local_url, local_url_to_path

logger = logging.getLogger(__name__)


def open_range_reader(slide_url: str):
    """Return a file-like object tifffile can read from, for any slide_url."""
    if is_local_url(slide_url):
        return open(local_url_to_path(slide_url), "rb")
    return _HTTPRangeFile(slide_url)


class _HTTPRangeFile:
    """
    Minimal file-like object (read/seek/tell/close) that tifffile can parse
    a TIFF's IFD chain + individual pages out of, fetching only the byte
    ranges it actually asks for.
    """

    def __init__(self, url: str) -> None:
        import httpx

        self._url = to_https(url)
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=5.0),
            follow_redirects=True,
        )
        self._headers = gcs_auth_headers(self._url)
        self._pos = 0
        head = self._client.head(self._url, headers=self._headers)
        self._size = int(head.headers.get("Content-Length", 0))

    def read(self, n: int = -1) -> bytes:
        if self._size == 0:
            return b""
        end = (self._size - 1) if (n is None or n < 0) else (min(self._pos + n, self._size) - 1)
        if end < self._pos:
            return b""
        headers = {**self._headers, "Range": f"bytes={self._pos}-{end}"}
        resp = self._client.get(self._url, headers=headers)
        data = resp.content
        self._pos += len(data)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._size + offset
        return self._pos

    def tell(self) -> int:
        return self._pos

    def close(self) -> None:
        self._client.close()


def to_https(url: str) -> str:
    if url.startswith("gs://"):
        return "https://storage.googleapis.com/" + url[len("gs://"):]
    return url


def gcs_auth_headers(url: str) -> dict:
    """Best-effort ADC auth header; falls back to unauthenticated (public bucket)."""
    try:
        import google.auth
        import google.auth.transport.requests

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
        return {"Authorization": f"Bearer {credentials.token}"}
    except Exception as exc:
        logger.debug(
            "_slide_io: no GCS credentials available (%s); trying unauthenticated request", exc,
        )
        return {}


def download_to_temp(slide_url: str, dest_path) -> None:
    """
    Stream *slide_url* to a local file at *dest_path* — used when a native
    tool (e.g. libvips) needs a real filesystem path and can't consume a
    Range-reading file-like object the way tifffile can.
    """
    fh = open_range_reader(slide_url)
    try:
        with open(dest_path, "wb") as out:
            while True:
                chunk = fh.read(8 * 1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    finally:
        fh.close()
