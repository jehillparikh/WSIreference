"""
services/gcs_stream_service.py
Handles all Google Cloud Storage interactions.

Design choices
──────────────
• Uses google-auth ADC (Application Default Credentials) throughout.
  On Cloud Run / GKE the Workload-Identity-bound service account is picked up
  automatically.  Locally, set GOOGLE_APPLICATION_CREDENTIALS or run
  ``gcloud auth application-default login``.

• Signed URLs:  only needed when the GCS bucket is private AND the browser
  needs to fetch tiles directly (the /api/slide-direct-url route).  The proxy
  path (/api/raw_slides/…) never needs a signed URL — it streams via the
  server-side ADC credential.

• Range proxying:  the service accepts a raw ``Range: bytes=a-b`` header
  value, forwards it to GCS, and returns (status, headers, body_bytes).
  The route layer turns those into an HTTP response.

No session logic, no slide-list building — those live in SlideSessionService.
"""
from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Bytes returned for a HEAD-only probe that just needs Content-Length
_HEAD_CHUNK = 0


class GCSStreamService:
    """
    Async GCS stream proxy.

    Designed for injection as a singleton.  Call .open() / .close() once
    at application startup / shutdown, or use as an async context manager.
    """

    def __init__(self, project_id: Optional[str] = None) -> None:
        self._project_id = project_id
        self._http: Optional[httpx.AsyncClient] = None
        # Lazy-loaded google-auth credentials
        self._credentials = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def open(self) -> None:
        self._credentials = self._load_adc()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "GCSStreamService":
        await self.open()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ── Public API ────────────────────────────────────────────────────────

    async def stream_range(
        self,
        gcs_url: str,
        range_header: Optional[str],
    ) -> tuple[int, dict, bytes]:
        """
        Proxy a Range request to *gcs_url*.

        Returns
        ───────
        (status_code, response_headers, body_bytes)

        status_code  206 on partial content, 200 on full, 4xx/5xx on error.
        response_headers includes Content-Range, Content-Type, Accept-Ranges.
        """
        assert self._http, "GCSStreamService not opened"

        req_headers: dict[str, str] = {"Accept-Ranges": "bytes"}
        if range_header:
            req_headers["Range"] = range_header

        auth_headers = await self._auth_headers(gcs_url)
        req_headers.update(auth_headers)

        resp = await self._http.get(gcs_url, headers=req_headers)

        # Passthrough headers the browser / OpenSeadragon needs
        forward = {
            "Content-Type": resp.headers.get("Content-Type", "application/octet-stream"),
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*",
        }
        if "Content-Range" in resp.headers:
            forward["Content-Range"] = resp.headers["Content-Range"]
        if "Content-Length" in resp.headers:
            forward["Content-Length"] = resp.headers["Content-Length"]

        return resp.status_code, forward, resp.content

    async def head_content_length(self, gcs_url: str) -> Optional[int]:
        """
        Issue a HEAD request and return Content-Length.
        Used by GeoTIFFTileSource to learn the file size before issuing Ranges.
        """
        assert self._http
        auth_headers = await self._auth_headers(gcs_url)
        resp = await self._http.head(gcs_url, headers=auth_headers)
        cl = resp.headers.get("Content-Length")
        return int(cl) if cl else None

    async def generate_signed_url(
        self,
        gcs_url: str,
        ttl_seconds: int = 3600,
        service_account_email: Optional[str] = None,
    ) -> Optional[str]:
        """
        Return a V4 signed URL for *gcs_url* so the browser can read GCS
        directly without going through the proxy.

        Requires either:
          • A service-account key file (GOOGLE_APPLICATION_CREDENTIALS), or
          • The Workload Identity SA to have iam.serviceAccounts.signBlob on
            itself (``--service-account`` on Cloud Run grants this by default).

        Returns None if signing isn't possible (e.g. user ADC credentials in
        local dev) — the caller falls back to the proxy URL.
        """
        try:
            from google.cloud import storage as gcs  # type: ignore

            bucket_name, blob_name = self._parse_gcs_url(gcs_url)
            if not bucket_name:
                return None

            client = gcs.Client(project=self._project_id)
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)

            import datetime

            url = blob.generate_signed_url(
                version="v4",
                expiration=datetime.timedelta(seconds=ttl_seconds),
                method="GET",
                service_account_email=service_account_email,
            )
            return url
        except Exception as exc:
            logger.debug("Signed URL generation skipped: %s", exc)
            return None

    # ── Internals ──────────────────────────────────────────────────────────

    @staticmethod
    def _load_adc():
        """
        Load Application Default Credentials without blocking the event loop.
        Returns the credentials object or None if google-auth isn't installed.
        """
        try:
            import google.auth  # type: ignore
            import google.auth.transport.requests  # type: ignore

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/devstorage.read_only"]
            )
            # Eagerly refresh so the first request doesn't block
            req = google.auth.transport.requests.Request()
            credentials.refresh(req)
            return credentials
        except Exception as exc:
            logger.warning(
                "GCSStreamService: could not load ADC (%s). "
                "Requests to private GCS buckets will fail.",
                exc,
            )
            return None

    async def _auth_headers(self, gcs_url: str) -> dict:
        """
        Return Authorization header dict for a GCS request.
        Refreshes the ADC token if it has expired.
        """
        if not self._credentials:
            return {}
        try:
            import google.auth.transport.requests  # type: ignore

            if not self._credentials.valid:
                req = google.auth.transport.requests.Request()
                self._credentials.refresh(req)  # sync — acceptable once per hour
            return {"Authorization": f"Bearer {self._credentials.token}"}
        except Exception as exc:
            logger.warning("GCSStreamService: token refresh failed: %s", exc)
            return {}

    @staticmethod
    def _parse_gcs_url(url: str) -> tuple[Optional[str], str]:
        """
        Parse ``https://storage.googleapis.com/<bucket>/<blob>``
        or ``gs://<bucket>/<blob>`` into (bucket, blob).
        """
        if url.startswith("gs://"):
            parts = url[5:].split("/", 1)
            return (parts[0], parts[1]) if len(parts) == 2 else (None, "")

        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lstrip("/")
        parts = path.split("/", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else (None, "")
