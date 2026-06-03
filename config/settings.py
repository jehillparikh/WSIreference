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

        # ── Server ─────────────────────────────────────────────────────────
        self.root_path: str = os.getenv("ROOT_PATH", "")
        self.port: int = int(os.getenv("PORT", "8080"))

    # ── Validation helpers ──────────────────────────────────────────────────

    @property
    def pathology_api_configured(self) -> bool:
        return bool(
            self.external_api_base_url
            and self.external_api_email
            and self.external_api_password
        )
