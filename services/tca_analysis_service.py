"""
services/tca_analysis_service.py
Tumour Content Analysis — parse TCA metadata into typed domain models.

Separation of concerns
──────────────────────
TCAOverlayService  (existing)
    • Downloads the TCA zip from GCS.
    • Extracts {stem}_density.png / {stem}_metadata.json / {stem}_grid.json
      into {overlay_cache_dir}/{stem}/.

TCAAnalysisService  (this file)
    • Reads the extracted JSON files once TCAOverlayService has made them
      available on disk.
    • Parses them into typed TCAAnalysisResult objects.
    • Computes slide-level aggregates (overall tumour %, hotspot, etc.).
    • Caches results in-memory so disk reads happen only once per stem.
    • Designed for drop-in replacement: swap the _parse_* internals to
      adapt to a different TCA pipeline JSON schema without touching routes.

Supported _metadata.json schemas
─────────────────────────────────
The parser is deliberately lenient and handles multiple conventions from
common TCA pipelines.  It tries field names in order of specificity:
    tumour_pct / tumor_pct / tumour_percentage / tumor_percentage
    stroma_pct / stroma_percentage
    necrosis_pct / necrosis_percentage
    cell_count / total_cells
Any unrecognised top-level keys are preserved verbatim in raw_metadata.

Thread safety: asyncio single-event-loop — asyncio.Lock per slide key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.tca_domain import (
    TCAAggregates,
    TCAAnalysisResult,
    TCADensityBand,
    TCAGridCell,
)
from services.tca_overlay_service import TCAOverlayService, _slide_stem

logger = logging.getLogger(__name__)


class TCAAnalysisService:
    """
    Parse TCA JSON assets into typed TCAAnalysisResult objects.

    Usage (singleton wired in main.py)
    ───────────────────────────────────
        result = await tca_analysis_svc.get_analysis(
            slide_key="1087-25.svs",
            overlay_svc=app.state.overlay_service,
        )
    """

    def __init__(self) -> None:
        # stem → TCAAnalysisResult
        self._cache: dict[str, TCAAnalysisResult] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Public API ────────────────────────────────────────────────────────

    async def get_analysis(
        self,
        slide_key: str,
        overlay_svc: TCAOverlayService,
    ) -> TCAAnalysisResult:
        """
        Return TCA analysis for *slide_key*.

        Flow:
          1. Check in-memory cache.
          2. Ask TCAOverlayService for the overlay config (triggers download if
             needed — but the overlay service owns that; we just read the paths).
          3. Parse _metadata.json → aggregates + density bands.
          4. Parse _grid.json → grid cells (optional).
          5. Cache + return.
        """
        stem = _slide_stem(slide_key)

        if stem in self._cache:
            return self._cache[stem]

        lock = self._get_lock(stem)
        async with lock:
            if stem in self._cache:
                return self._cache[stem]

            result = await self._build_result(stem, overlay_svc)
            self._cache[stem] = result
            return result

    def invalidate(self, slide_key: str) -> None:
        """Remove cached result so the next call re-parses from disk."""
        stem = _slide_stem(slide_key)
        self._cache.pop(stem, None)
        logger.debug("TCAAnalysisService: invalidated cache for %s", stem)

    def list_analysed_slides(self) -> list[str]:
        """Return stems that have been analysed and are cached."""
        return [k for k, v in self._cache.items() if v.available]

    # ── Internals ──────────────────────────────────────────────────────────

    async def _build_result(
        self,
        stem: str,
        overlay_svc: TCAOverlayService,
    ) -> TCAAnalysisResult:
        # We re-use overlay_svc's config only to find the asset paths —
        # we do NOT re-download here; that's TCAOverlayService's job.
        config = overlay_svc._build_config_from_dir(
            overlay_svc._cache_dir / stem,
            slide_metadata=None,
        )

        if not config.available or not config.metadata_path:
            logger.debug("TCAAnalysisService: no assets for stem=%s", stem)
            return TCAAnalysisResult(slide_key=stem, available=False)

        try:
            meta_raw = json.loads(Path(config.metadata_path).read_text())
        except Exception as exc:
            logger.warning("TCAAnalysisService: failed to read metadata for %s: %s", stem, exc)
            return TCAAnalysisResult(slide_key=stem, available=False)

        # Parse grid (optional)
        grid_cells: list[TCAGridCell] = []
        if config.grid_path:
            try:
                grid_raw = json.loads(Path(config.grid_path).read_text())
                grid_cells = _parse_grid(grid_raw)
            except Exception as exc:
                logger.warning("TCAAnalysisService: grid parse failed for %s: %s", stem, exc)

        density_bands = _parse_density_bands(meta_raw)
        aggregates = _compute_aggregates(meta_raw, grid_cells)

        result = TCAAnalysisResult(
            slide_key=stem,
            available=True,
            aggregates=aggregates,
            density_bands=density_bands,
            grid_cells=grid_cells,
            analysed_at=datetime.utcnow(),
            raw_metadata=meta_raw,
        )
        logger.info(
            "TCAAnalysisService: parsed stem=%s  tumour_pct=%.1f  cells=%d",
            stem,
            aggregates.overall_tumour_pct or 0.0,
            len(grid_cells),
        )
        return result

    def _get_lock(self, stem: str) -> asyncio.Lock:
        if stem not in self._locks:
            self._locks[stem] = asyncio.Lock()
        return self._locks[stem]


# ── Parsing helpers ────────────────────────────────────────────────────────


def _pick(d: dict, *keys: str, default=None):
    """Return the first matching key from *d*, or *default*."""
    for k in keys:
        if k in d:
            return d[k]
    return default


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


def _parse_density_bands(meta: dict) -> list[TCADensityBand]:
    """
    Parse density band data from _metadata.json.

    Supports two conventions:
    • List under "density_bands" / "bands" key.
    • Flat keys: low_count, medium_count, high_count (common in older pipelines).
    """
    bands_raw = _pick(meta, "density_bands", "bands")
    if isinstance(bands_raw, list):
        out: list[TCADensityBand] = []
        for b in bands_raw:
            if not isinstance(b, dict):
                continue
            label = str(_pick(b, "label", "name", default="unknown"))
            out.append(TCADensityBand(
                label=label,
                min_pct=_safe_float(_pick(b, "min_pct", "min", "min_percentage")) or 0.0,
                max_pct=_safe_float(_pick(b, "max_pct", "max", "max_percentage")) or 100.0,
                cell_count=_safe_int(_pick(b, "cell_count", "count", "cells")) or 0,
                area_um2=_safe_float(_pick(b, "area_um2", "area")),
            ))
        return out

    # Flat convention: low_count / medium_count / high_count
    flat_bands: list[TCADensityBand] = []
    for label, lo, hi in [("low", 0.0, 33.3), ("medium", 33.3, 66.6), ("high", 66.6, 100.0)]:
        count_key = f"{label}_count"
        count = _safe_int(_pick(meta, count_key))
        if count is not None:
            flat_bands.append(TCADensityBand(
                label=label,
                min_pct=lo,
                max_pct=hi,
                cell_count=count,
                area_um2=_safe_float(_pick(meta, f"{label}_area_um2", f"{label}_area")),
            ))
    return flat_bands


def _parse_grid(grid_raw) -> list[TCAGridCell]:
    """
    Parse _grid.json into a list of TCAGridCell objects.

    Supported shapes:
    • List of cell dicts: [{"row": 0, "col": 0, "tumour_pct": 45.2, ...}, ...]
    • 2-D array of dicts: [[{"tumour_pct": ...}, ...], ...]
    • 2-D array of floats (tumour % only): [[45.2, 30.1, ...], ...]
    """
    cells: list[TCAGridCell] = []

    if isinstance(grid_raw, list):
        if not grid_raw:
            return cells

        # Flat list of dicts
        if isinstance(grid_raw[0], dict) and "row" in grid_raw[0]:
            for c in grid_raw:
                cells.append(_cell_from_dict(c.get("row", 0), c.get("col", 0), c))
            return cells

        # 2-D structure
        for r, row in enumerate(grid_raw):
            if isinstance(row, list):
                for c, cell in enumerate(row):
                    if isinstance(cell, dict):
                        cells.append(_cell_from_dict(r, c, cell))
                    elif isinstance(cell, (int, float)):
                        cells.append(TCAGridCell(
                            row=r, col=c,
                            tumour_pct=float(cell),
                            stroma_pct=max(0.0, 100.0 - float(cell)),
                        ))

    return cells


def _cell_from_dict(row: int, col: int, d: dict) -> TCAGridCell:
    return TCAGridCell(
        row=row,
        col=col,
        tumour_pct=_safe_float(_pick(d, "tumour_pct", "tumor_pct", "tumour", "tumor")) or 0.0,
        stroma_pct=_safe_float(_pick(d, "stroma_pct", "stroma")) or 0.0,
        necrosis_pct=_safe_float(_pick(d, "necrosis_pct", "necrosis")),
        inflammation_pct=_safe_float(_pick(d, "inflammation_pct", "inflammation")),
        other_pct=_safe_float(_pick(d, "other_pct", "other")),
    )


def _compute_aggregates(meta: dict, grid_cells: list[TCAGridCell]) -> TCAAggregates:
    """
    Compute slide-level aggregates.

    Priority:
    1. Use pre-computed values from _metadata.json if present.
    2. Derive from grid_cells if metadata lacks aggregate fields.
    """
    # Try pre-computed overall values first
    overall_tumour = _safe_float(
        _pick(meta, "overall_tumour_pct", "tumour_pct", "tumor_pct",
              "overall_tumor_pct", "tumour_percentage", "tumor_percentage")
    )
    overall_stroma = _safe_float(
        _pick(meta, "overall_stroma_pct", "stroma_pct", "stroma_percentage")
    )
    overall_necrosis = _safe_float(
        _pick(meta, "overall_necrosis_pct", "necrosis_pct", "necrosis_percentage")
    )
    hotspot = _safe_float(_pick(meta, "hotspot_tumour_pct", "hotspot_tumor_pct", "hotspot"))
    hotspot_row = _safe_int(_pick(meta, "hotspot_row"))
    hotspot_col = _safe_int(_pick(meta, "hotspot_col"))

    tissue_cells = [c for c in grid_cells if c.tumour_pct + c.stroma_pct > 0]

    # Derive from grid if metadata didn't supply aggregates
    if overall_tumour is None and tissue_cells:
        overall_tumour = sum(c.tumour_pct for c in tissue_cells) / len(tissue_cells)
    if overall_stroma is None and tissue_cells:
        overall_stroma = sum(c.stroma_pct for c in tissue_cells) / len(tissue_cells)
    if overall_necrosis is None and tissue_cells:
        nec = [c.necrosis_pct for c in tissue_cells if c.necrosis_pct is not None]
        overall_necrosis = sum(nec) / len(nec) if nec else None

    if hotspot is None and grid_cells:
        hotspot_cell = max(grid_cells, key=lambda c: c.tumour_pct)
        hotspot = hotspot_cell.tumour_pct
        hotspot_row = hotspot_cell.row
        hotspot_col = hotspot_cell.col

    return TCAAggregates(
        overall_tumour_pct=overall_tumour,
        overall_stroma_pct=overall_stroma,
        overall_necrosis_pct=overall_necrosis,
        hotspot_tumour_pct=hotspot,
        hotspot_row=hotspot_row,
        hotspot_col=hotspot_col,
        total_cells_analysed=len(grid_cells),
        tissue_cells=len(tissue_cells),
    )
