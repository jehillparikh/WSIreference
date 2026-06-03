"""
clients/pathology_api.py
Thin async HTTP client for the external pathology / EMR API.

Responsibilities
────────────────
• POST /user/login  →  acquire authToken
• Token is stored in-process and re-acquired on 401 or explicit expiry.
• GET /pathology_image/all  →  returns raw JSON payload
• No session or overlay logic lives here — those belong in services.

The caller (SlideSessionService) owns retry / session-rebuild policy.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from config.settings import Settings

logger = logging.getLogger(__name__)

_LOGIN_PATH = "/user/login"
_SLIDES_PATH = "/pathology_image/all"


class PathologyAPIError(Exception):
    """Raised when the pathology API returns an unexpected response."""

    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(f"Pathology API {status}: {detail}")


class PathologyAPIClient:
    """
    Async client around the external pathology/EMR API.

    Usage
    ─────
        async with PathologyAPIClient(settings) as client:
            slides_payload = await client.fetch_slides(
                patient_id="...", event_id="...", slide_id="..."
            )

    Or inject a singleton and call .ensure_token() / .fetch_slides() directly.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.external_api_base_url.rstrip("/")
        self._email = settings.external_api_email
        self._password = settings.external_api_password

        self._token: Optional[str] = None
        self._http: Optional[httpx.AsyncClient] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def __aenter__(self) -> "PathologyAPIClient":
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0),
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    async def open(self) -> None:
        """For singleton / dependency-injection usage outside async-with."""
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0),
        )

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Public API ────────────────────────────────────────────────────────

    async def login(self) -> str:
        """
        POST /user/login  →  store + return authToken.
        Raises PathologyAPIError on non-2xx.
        """
        assert self._http, "Client not opened — use async-with or call .open()"
        logger.info("PathologyAPIClient: logging in as %s", self._email)

        resp = await self._http.post(
            _LOGIN_PATH,
            json={"email": self._email, "password": self._password},
        )
        if resp.status_code not in (200, 201):
            raise PathologyAPIError(resp.status_code, resp.text[:200])

        body = resp.json()
        token = (body.get("payLoad") or {}).get("authToken") or body.get("authToken")
        if not token:
            raise PathologyAPIError(resp.status_code, "authToken missing in login response")

        self._token = token
        logger.debug("PathologyAPIClient: token acquired")
        return self._token

    async def ensure_token(self) -> str:
        """Return cached token; re-login if it has been cleared."""
        if not self._token:
            await self.login()
        return self._token  # type: ignore[return-value]

    async def fetch_slides(
        self,
        patient_id: str,
        event_id: str,
        slide_id: str,
    ) -> dict:
        """
        GET /pathology_image/all?patient_id=…&event_id=…&slide_id=…

        Returns the raw ``payLoad`` dict from the API response.
        Automatically re-logs in once on 401.

        Expected shape:
            {
              "selected_slide_id": "B1",
              "blocks": [
                { "block_id": "A1", "slides": [ { slide object }, … ] }
              ]
            }
        """
        token = await self.ensure_token()
        raw = await self._get_slides(patient_id, event_id, slide_id, token)

        # ── Re-auth on 401 ─────────────────────────────────────────────────
        if raw is None:
            logger.info("PathologyAPIClient: 401 — re-logging in")
            await self.login()
            raw = await self._get_slides(
                patient_id, event_id, slide_id, self._token  # type: ignore
            )

        if raw is None:
            raise PathologyAPIError(401, "Re-authentication failed")

        return raw

    # ── Internals ──────────────────────────────────────────────────────────

    async def _get_slides(
        self,
        patient_id: str,
        event_id: str,
        slide_id: str,
        token: str,
    ) -> Optional[dict]:
        """
        Returns parsed payLoad dict, or None on 401 (caller retries).
        Raises PathologyAPIError on other errors.
        """
        assert self._http

        resp = await self._http.get(
            _SLIDES_PATH,
            params={
                "patient_id": patient_id,
                "event_id": event_id,
                "slide_id": slide_id,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        if resp.status_code == 401:
            self._token = None  # force re-login
            return None

        if not resp.is_success:
            raise PathologyAPIError(resp.status_code, resp.text[:200])

        body = resp.json()
        payload = body.get("payLoad") or body
        return payload
