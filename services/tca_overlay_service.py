"""
services/tca_overlay_service.py
Tumor Cell Annotation overlay asset management.

Lifecycle
─────────
1. First request for a slide's overlay → download tca_url zip from GCS,
   extract into {overlay_cache_dir}/{slide_stem}/.
2. Subsequent requests → serve from cache.
3. Assets are never pushed back to GCS — the browser fetches them through
   /api/overlay-file/{filename} which this service resolves.

File layout inside the zip (by convention):
    {stem}_density.png
    {stem}_metadata.json
    {stem}_grid.json          (optional)

The service identifies files by suffix, not by exact name, so minor
naming variations from the API are tolerated.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import zipfile
from pathlib import Path
from typing import Optional

import httpx

from config.settings import Settings, get_settings
from models.domain import OverlayConfig, SlideMetadata

logger = logging.getLogger(__name__)


class TCAOverlayService:
    """
    Download, extract, and serve TCA overlay assets.

    Designed as a singleton.  Thread-safe via asyncio.Lock per slide stem.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._cache_dir = Path(self._settings.overlay_cache_dir)
        self._http: Optional[httpx.AsyncClient] = None
        # Per-stem locks prevent duplicate downloads under concurrent requests
        self._stem_locks: dict[str, asyncio.Lock] = {}
        # Cached OverlayConfig objects (avoid re-scanning disk on every request)
        self._config_cache: dict[str, OverlayConfig] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def open(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0),
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    # ── Public API ────────────────────────────────────────────────────────

    async def get_overlay_config(
        self,
        slide_name: str,
        tca_url: Optional[str],
        slide_metadata: Optional[SlideMetadata],
    ) -> OverlayConfig:
        """
        Return an OverlayConfig for *slide_name*.

        • If assets are already cached on disk, build config from them.
        • Otherwise download + extract the zip from *tca_url*.
        • If tca_url is None/empty, returns OverlayConfig(available=False).
        """
        stem = _slide_stem(slide_name)

        if not tca_url:
            return OverlayConfig(available=False)

        # ── Check in-memory config cache ──────────────────────────────────
        if stem in self._config_cache:
            return self._config_cache[stem]

        lock = self._get_lock(stem)
        async with lock:
            # Double-check after acquiring lock
            if stem in self._config_cache:
                return self._config_cache[stem]

            # ── Check disk cache ───────────────────────────────────────────
            slide_dir = self._cache_dir / stem
            config = self._build_config_from_dir(slide_dir, slide_metadata)
            if config.available:
                self._config_cache[stem] = config
                return config

            # ── Download + extract ─────────────────────────────────────────
            try:
                await self._download_and_extract(tca_url, slide_dir)
            except Exception as exc:
                logger.warning("TCAOverlayService: failed to fetch %s: %s", tca_url, exc)
                return OverlayConfig(available=False)

            config = self._build_config_from_dir(slide_dir, slide_metadata)
            self._config_cache[stem] = config
            return config

    def resolve_asset_path(self, filename: str) -> Optional[Path]:
        """
        Resolve *filename* (e.g. ``1087-25_density.png``) to an absolute
        path on disk.  Returns None if the file does not exist.
        """
        # Search all stem sub-directories
        for sub in self._cache_dir.iterdir():
            if sub.is_dir():
                candidate = sub / filename
                if candidate.exists():
                    return candidate
        return None

    def invalidate(self, slide_name: str) -> None:
        """Remove cached config so the next request re-checks disk."""
        stem = _slide_stem(slide_name)
        self._config_cache.pop(stem, None)

    # ── Internals ──────────────────────────────────────────────────────────

    async def _download_and_extract(self, tca_url: str, dest_dir: Path) -> None:
        assert self._http, "TCAOverlayService not opened"
        logger.info("TCAOverlayService: downloading %s", tca_url)

        resp = await self._http.get(tca_url)
        resp.raise_for_status()

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Run zipfile extraction in a thread pool to avoid blocking the loop
        zip_bytes = resp.content
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _extract_zip, zip_bytes, dest_dir)
        logger.info("TCAOverlayService: extracted to %s", dest_dir)

    def _build_config_from_dir(
        self,
        slide_dir: Path,
        slide_metadata: Optional[SlideMetadata],
    ) -> OverlayConfig:
        if not slide_dir.exists():
            return OverlayConfig(available=False)

        density = _find_by_suffix(slide_dir, "_density.png")
        metadata_json = _find_by_suffix(slide_dir, "_metadata.json")
        grid = _find_by_suffix(slide_dir, "_grid.json")

        # Both density image AND metadata JSON must be present (per spec)
        available = density is not None and metadata_json is not None

        return OverlayConfig(
            available=available,
            density_image_path=str(density) if density else None,
            metadata_path=str(metadata_json) if metadata_json else None,
            grid_path=str(grid) if grid else None,
            slide_metadata=slide_metadata,
        )

    def _get_lock(self, stem: str) -> asyncio.Lock:
        if stem not in self._stem_locks:
            self._stem_locks[stem] = asyncio.Lock()
        return self._stem_locks[stem]


# ── Module-level helpers ───────────────────────────────────────────────────


def _slide_stem(slide_name: str) -> str:
    """
    Normalise slide_name to a filesystem-safe stem.
    e.g. ``"1087-25.svs"``  → ``"1087-25"``
         ``"B1"``            → ``"B1"``
    """
    return Path(slide_name).stem


def _extract_zip(zip_bytes: bytes, dest_dir: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        zf.extractall(dest_dir)


def _find_by_suffix(directory: Path, suffix: str) -> Optional[Path]:
    """Return first file in *directory* whose name ends with *suffix*."""
    for f in directory.iterdir():
        if f.is_file() and f.name.endswith(suffix):
            return f
    return None
