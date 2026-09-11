"""
clients/mock_pathology_api.py
Drop-in mock for PathologyAPIClient. Active when EXTERNAL_API_BASE_URL=mock.

Design
──────
• Scans MOCK_SLIDE_DIR for .tiff / .svs / .ndpi / .tif files at fetch time.
• Returns a realistic payLoad dict with local:// slide URLs that
  GCSStreamService reads directly from disk (with full Range support).
• patient_id and event_id are accepted but ignored — any values work.
• selected_slide_id is echoed back so SlideSessionService can mark a default.

This class has the SAME async interface as PathologyAPIClient so that
SlideSessionService can swap them without knowing which is active.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from config.settings import Settings

logger = logging.getLogger(__name__)

# File extensions the viewer can handle via GeoTIFFTileSource
_SLIDE_EXTENSIONS = {".tiff", ".tif", ".svs", ".ndpi", ".scn", ".czi"}


class MockPathologyAPIClient:
    """In-process mock — no network calls, no credentials needed."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._slide_dir = _resolve_slide_dir(settings.mock_slide_dir)

    # ── Lifecycle (no-ops) ─────────────────────────────────────────────────

    async def open(self) -> None:
        logger.info(
            "MockPathologyAPIClient: active — serving slides from %s",
            self._slide_dir,
        )

    async def close(self) -> None:
        pass

    async def login(self) -> str:
        return "mock-token"

    async def ensure_token(self) -> str:
        return "mock-token"

    # ── Public API ─────────────────────────────────────────────────────────

    async def fetch_slides(
        self,
        patient_id: str,
        event_id: str,
        slide_id: str,
    ) -> dict:
        """
        Return a payLoad dict that mimics the real pathology API response.

        patient_id / event_id are ignored; slide_id is echoed back as
        selected_slide_id so the session service can set the default slide.
        """
        slide_files = self._find_slide_files()

        if not slide_files:
            logger.warning(
                "MockPathologyAPIClient: no slide files found in %s. "
                "Run  python mock_slides/create_test_slide.py  to generate one.",
                self._slide_dir,
            )
            return {"selected_slide_id": slide_id, "blocks": []}

        slides = []
        for path in slide_files:
            local_url = _path_to_local_url(path)
            slides.append({
                # slide_id matches the stem so selected_slide_id can resolve to it
                "slide_id": path.stem,
                "slide_url": local_url,
                "thumbnail_url": local_url,   # same file; viewer proxies it
                "tca_url": None,
                "status": "FINISHED",
                "meta_data": {
                    "width": 4096,
                    "height": 3072,
                    "mpp": 0.25,
                    "objective_power": 40,
                    "vendor": "mock",
                },
            })

        logger.info(
            "MockPathologyAPIClient: returning %d slide(s) for "
            "patient_id=%r event_id=%r selected_slide_id=%r",
            len(slides),
            patient_id,
            event_id,
            slide_id,
        )

        return {
            "selected_slide_id": slide_id,
            "blocks": [{"block_id": "MOCK", "slides": slides}],
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    def _find_slide_files(self) -> List[Path]:
        if not self._slide_dir.exists():
            logger.warning(
                "MockPathologyAPIClient: slide directory %s does not exist. "
                "Create it and add slide files, or run create_test_slide.py.",
                self._slide_dir,
            )
            return []

        files = sorted(
            p for p in self._slide_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _SLIDE_EXTENSIONS
        )
        logger.info(
            "MockPathologyAPIClient: found %d slide file(s): %s",
            len(files),
            [f.name for f in files],
        )
        return files


# ── Module-level helpers ───────────────────────────────────────────────────

def _resolve_slide_dir(mock_slide_dir: str) -> Path:
    """Resolve mock_slide_dir relative to the project root if not absolute."""
    p = Path(mock_slide_dir)
    if not p.is_absolute():
        # clients/ → project root (two levels up from this file)
        project_root = Path(__file__).parent.parent
        p = project_root / p
    return p.resolve()


def _path_to_local_url(path: Path) -> str:
    """
    Convert a filesystem Path to a local:// URL understood by GCSStreamService.

    Uses pathlib.as_uri() to correctly handle Windows drive letters and spaces.

    Examples
    ─────────
      Windows:  Path("D:/slides/sample.tiff") → "local:///D:/slides/sample.tiff"
      Linux:    Path("/data/slides/sample.tiff") → "local:///data/slides/sample.tiff"
    """
    file_uri = path.as_uri()                            # file:///D:/slides/sample.tiff
    return "local://" + file_uri[len("file://"):]       # local:///D:/slides/sample.tiff
