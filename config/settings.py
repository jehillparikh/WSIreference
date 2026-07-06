"""
config/settings.py
Centralised configuration.  All values come from environment variables
(12-factor) with sensible defaults.  integrate_config.json is treated as a
low-priority fallback so the viewer works in dev without env vars.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional


def _load_integrate_config() -> dict:
    """Load integrate_config.json if it exists (dev fallback)."""
    path = Path(os.getenv("INTEGRATE_CONFIG_PATH", "integrate_config.json"))
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def _load_dotenv() -> None:
    """
    Minimal .env loader — no external dependencies required.

    Reads KEY=VALUE pairs from the .env file in the project root and sets
    them in os.environ, but ONLY if the key is not already present.
    This means real environment variables (Cloud Run secrets, CI vars) always
    win over the .env file, which is correct 12-factor behaviour.

    Lines starting with '#' and blank lines are silently skipped.
    Inline comments are NOT supported (matches standard .env semantics).
    """
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


# Load .env at import time so os.getenv() calls in Settings.__init__ see the values.
_load_dotenv()


@lru_cache(maxsize=1)
def get_settings() -> "Settings":
    return Settings()


class Settings:
    """
    Runtime configuration.  Environment variables always win over
    integrate_config.json so secrets can be injected via Cloud Run /
    Kubernetes without baking them into the image.
    """

    def __init__(self) -> None:
        _cfg = _load_integrate_config()

        # ── External pathology API ──────────────────────────────────────────
        self.external_api_base_url: str = (
            os.getenv("EXTERNAL_API_BASE_URL") or _cfg.get("base_url", "")
        )
        self.external_api_email: str = (
            os.getenv("EXTERNAL_API_EMAIL") or _cfg.get("email", "")
        )
        self.external_api_password: str = (
            os.getenv("EXTERNAL_API_PASSWORD") or _cfg.get("password", "")
        )

        # ── Session lifecycle ───────────────────────────────────────────────
        self.wsi_url_refresh_minutes: int = int(
            os.getenv("WSI_URL_REFRESH_MINUTES", "10")
        )
        self.session_ttl_minutes: int = int(
            os.getenv("SESSION_TTL", "30")
        )

        # ── GCP ────────────────────────────────────────────────────────────
        # ADC is used by default (Workload Identity on GKE / Cloud Run,
        # or GOOGLE_APPLICATION_CREDENTIALS for a service-account key file).
        # gcs_service_account is only needed for explicit impersonation.
        self.gcs_project_id: Optional[str] = os.getenv("GCP_PROJECT_ID")
        self.gcs_service_account: Optional[str] = os.getenv(
            "GCS_SERVICE_ACCOUNT_EMAIL"
        )
        # Signed-URL lifetime in seconds (used when GCS bucket is private
        # and the viewer needs to hand the URL to the browser directly).
        self.gcs_signed_url_ttl_seconds: int = int(
            os.getenv("GCS_SIGNED_URL_TTL_SECONDS", "3600")
        )

        # ── Overlay asset cache ─────────────────────────────────────────────
        self.overlay_cache_dir: str = os.getenv(
            "OVERLAY_CACHE_DIR", "/tmp/wsi_overlays"
        )

        # ── Thumbnail cache ──────────────────────────────────────────────────
        # Generated overview PNGs (see ThumbnailService), keyed by a hash of
        # the slide's raw URL. Works for any slide reachable via slide_url —
        # local:// (mock mode) or gs:// / https:// (real GCS) — independent
        # of whatever thumbnail_url the pathology API reports.
        self.thumbnail_cache_dir: str = os.getenv(
            "THUMBNAIL_CACHE_DIR", "/tmp/wsi_thumbnails"
        )

        # ── Normalized slide cache ───────────────────────────────────────────
        # Clean, re-encoded pyramidal TIFFs (see SlideNormalizationService),
        # produced in the background for slides whose original format isn't
        # well-supported by the client-side tile viewer. The original file
        # is never modified — this is purely a derived, disposable cache.
        self.normalized_slide_cache_dir: str = os.getenv(
            "NORMALIZED_SLIDE_CACHE_DIR", "/tmp/wsi_normalized_slides"
        )

        # ── Mock / local mode ───────────────────────────────────────────────
        # Activated automatically when EXTERNAL_API_BASE_URL=mock.
        # All three settings are ignored when the real pathology API is used.
        self.use_mock_api: bool = (
            self.external_api_base_url.strip().lower() == "mock"
        )
        # Directory that contains local .tiff/.svs files for mock sessions.
        self.mock_slide_dir: str = os.getenv("MOCK_SLIDE_DIR", "mock_slides")
        # Base URL the mock client embeds in slide_url values (must match where
        # the FastAPI server is reachable so the GCS proxy can resolve them).
        self.mock_base_url: str = os.getenv("MOCK_BASE_URL", "http://localhost:8080")

        # ── Server ─────────────────────────────────────────────────────────
        self.root_path: str = os.getenv("ROOT_PATH", "")
        self.port: int = int(os.getenv("PORT", "8080"))

    # ── Validation helpers ──────────────────────────────────────────────────

    @property
    def pathology_api_configured(self) -> bool:
        # Mock mode is always "configured" — no real credentials needed.
        if self.use_mock_api:
            return True
        return bool(
            self.external_api_base_url
            and self.external_api_email
            and self.external_api_password
        )
