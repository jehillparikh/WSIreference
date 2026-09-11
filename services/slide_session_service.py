"""
services/slide_session_service.py
Core orchestration service for WSI viewer sessions.

Responsibilities
────────────────
• get_or_create_session()  — the single entry-point that all routes call.
• Session key:  pid_<patient_id>_<event_id>_<selected_slide_id>
• Reuse if younger than WSI_URL_REFRESH_MINUTES.
• Rebuild (re-login + re-fetch slides) if stale.
• Evict sessions idle longer than SESSION_TTL.
• Map raw API slide objects → typed Slide domain models.
• Derive slide filename from the GCS URL (used as the routing key in
  /api/raw_slides/{filename} and /api/thumbnail/{slide_name}).

This service has NO knowledge of HTTP, FastAPI, or GCS internals.
"""
from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

from clients.pathology_api import PathologyAPIClient, PathologyAPIError
from config.settings import Settings, get_settings
from models.domain import Slide, SlideMetadata, WSISession

logger = logging.getLogger(__name__)


class SessionNotAvailableError(Exception):
    """
    Raised when a session cannot be built because the pathology API
    is not configured (HTTP 503 to the browser).
    """


class SlideSessionService:
    """
    In-memory session store.  One singleton per process.

    Thread safety: asyncio single-event-loop — no locking needed.
    For multi-process deployments (e.g. Cloud Run with concurrency > 1)
    consider replacing _store with a Redis-backed equivalent; the interface
    stays identical.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        api_client: Optional[PathologyAPIClient] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_client = api_client  # injected in tests; created lazily otherwise
        self._store: dict[str, WSISession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Call at application startup to launch the idle-session cleanup loop."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._api_client:
            await self._api_client.close()

    # ── Public API ────────────────────────────────────────────────────────

    async def get_or_create_session(
        self,
        patient_id: str,
        event_id: str,
        selected_slide_id: str,
    ) -> WSISession:
        """
        Return an active session, creating or refreshing as needed.

        Raises SessionNotAvailableError if the pathology API isn't configured.
        Raises PathologyAPIError if the API call fails.
        """
        if not self._settings.pathology_api_configured:
            raise SessionNotAvailableError(
                "Pathology API credentials are not configured."
            )

        key = _make_key(patient_id, event_id, selected_slide_id)

        async with self._lock:
            session = self._store.get(key)

            if session is None:
                logger.info("SlideSessionService: creating session %s", key)
                session = await self._build_session(
                    patient_id, event_id, selected_slide_id
                )
                self._store[key] = session
                return session

            # ── Refresh stale signed URLs ──────────────────────────────────
            refresh_threshold = timedelta(
                minutes=self._settings.wsi_url_refresh_minutes
            )
            age = datetime.utcnow() - session.refreshed_at
            if age > refresh_threshold:
                logger.info(
                    "SlideSessionService: refreshing stale session %s (age=%s)",
                    key,
                    age,
                )
                refreshed = await self._build_session(
                    patient_id, event_id, selected_slide_id
                )
                refreshed.created_at = session.created_at  # preserve original birth
                self._store[key] = refreshed
                return refreshed

            return session

    def get_session(self, patient_id: str, event_id: str, selected_slide_id: str) -> Optional[WSISession]:
        """
        Synchronous look-up (no refresh).  Used by routes that only need
        the already-built session — e.g. raw_slides proxy.
        """
        key = _make_key(patient_id, event_id, selected_slide_id)
        return self._store.get(key)

    def invalidate(self, patient_id: str, event_id: str, selected_slide_id: str) -> None:
        """Force-remove a session so the next request rebuilds it."""
        key = _make_key(patient_id, event_id, selected_slide_id)
        self._store.pop(key, None)

    # ── Session building ───────────────────────────────────────────────────

    async def _build_session(
        self,
        patient_id: str,
        event_id: str,
        selected_slide_id: str,
    ) -> WSISession:
        client = await self._get_client()

        payload = await client.fetch_slides(
            patient_id=patient_id,
            event_id=event_id,
            slide_id=selected_slide_id,
        )

        slides = _parse_slides(payload)

        # Sort so selected_slide_id comes first (becomes default_slide in UI)
        slides.sort(key=lambda s: (s.slide_id != selected_slide_id,))
        default = next((s for s in slides if s.slide_id == selected_slide_id), None) or (
            slides[0] if slides else None
        )

        return WSISession(
            patient_id=patient_id,
            event_id=event_id,
            selected_slide_id=selected_slide_id,
            slides=slides,
            default_slide=default,
        )

    # ── API client management ─────────────────────────────────────────────

    async def _get_client(self) -> PathologyAPIClient:
        if self._api_client is None:
            if self._settings.use_mock_api:
                # Lazy import keeps mock code out of the production import path.
                from clients.mock_pathology_api import MockPathologyAPIClient
                self._api_client = MockPathologyAPIClient(self._settings)  # type: ignore[assignment]
            else:
                self._api_client = PathologyAPIClient(self._settings)
            await self._api_client.open()
        return self._api_client

    # ── Background cleanup ────────────────────────────────────────────────

    async def _cleanup_loop(self) -> None:
        """Evict sessions idle longer than SESSION_TTL. Runs every minute."""
        while True:
            try:
                await asyncio.sleep(60)
                ttl = timedelta(minutes=self._settings.session_ttl_minutes)
                now = datetime.utcnow()
                stale = [
                    key
                    for key, sess in self._store.items()
                    if (now - sess.refreshed_at) > ttl
                ]
                for key in stale:
                    logger.debug("SlideSessionService: evicting idle session %s", key)
                    del self._store[key]
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("SlideSessionService cleanup error: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_key(patient_id: str, event_id: str, selected_slide_id: str) -> str:
    return f"pid_{patient_id}_{event_id}_{selected_slide_id}"


def _parse_slides(payload: dict) -> list[Slide]:
    """
    Convert the raw ``payLoad`` dict from GET /pathology_image/all
    into a list of typed Slide objects.

    Only slides with a non-empty slide_url are included (per spec).
    """
    slides: list[Slide] = []
    blocks = payload.get("blocks", [])

    for block in blocks:
        block_id = block.get("block_id", "")
        for raw in block.get("slides", []):
            slide_url = (raw.get("slide_url") or "").strip()
            if not slide_url:
                continue

            raw_meta = raw.get("meta_data") or {}
            meta = SlideMetadata(
                width=int(raw_meta.get("width", 0) or 0),
                height=int(raw_meta.get("height", 0) or 0),
                mpp=_safe_float(raw_meta.get("mpp")),
                objective_power=_safe_int(raw_meta.get("objective_power")),
                vendor=raw_meta.get("vendor"),
            )

            slide = Slide(
                slide_id=raw.get("slide_id", ""),
                block_id=block_id,
                slide_url=slide_url,
                thumbnail_url=raw.get("thumbnail_url"),
                tca_url=raw.get("tca_url"),
                status=raw.get("status", ""),
                meta_data=meta,
                filename=_filename_from_url(slide_url),
            )
            slides.append(slide)

    return slides


def _filename_from_url(url: str) -> str:
    """Extract the base filename from a GCS URL — used as the route key."""
    path = urllib.parse.urlparse(url).path
    return os.path.basename(path)


def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
