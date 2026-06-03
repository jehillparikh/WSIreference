"""
routes/tca_routes.py
HTTP API for Tumour Content Analysis (TCA) per slide.

Route map
─────────
GET    /api/tca/{slide_key}             → full TCAAnalysisResult
GET    /api/tca/{slide_key}/summary     → lightweight aggregate only
GET    /api/tca/{slide_key}/grid        → grid cells array (heatmap data)
DELETE /api/tca/{slide_key}/cache       → invalidate cached result

All GET routes accept the standard three query params (patient_id, event_id,
selected_slide_id) for session-scoping consistency, though the TCA analysis
itself is keyed purely by slide_key (filename stem).

The route layer is intentionally thin:
  1. Extract + validate path / query params.
  2. Delegate to TCAAnalysisService (which in turn reads from TCAOverlayService
     assets on disk).
  3. Serialise domain objects → JSON.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from services.tca_analysis_service import TCAAnalysisService
from services.tca_overlay_service import TCAOverlayService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tca", tags=["tca"])


# ── Service singletons ────────────────────────────────────────────────────


def _tca_analysis_svc(request: Request) -> TCAAnalysisService:
    return request.app.state.tca_analysis_service


def _overlay_svc(request: Request) -> TCAOverlayService:
    return request.app.state.overlay_service


# ── Serialisers ───────────────────────────────────────────────────────────


def _ser_result(result) -> dict:
    return {
        "slide_key": result.slide_key,
        "available": result.available,
        "analysed_at": result.analysed_at.isoformat() if result.analysed_at else None,
        "aggregates": _ser_aggregates(result.aggregates) if result.aggregates else None,
        "density_bands": [_ser_band(b) for b in result.density_bands],
        "grid_cells": [_ser_cell(c) for c in result.grid_cells],
    }


def _ser_summary(result) -> dict:
    agg = result.aggregates
    return {
        "slide_key": result.slide_key,
        "available": result.available,
        "analysed_at": result.analysed_at.isoformat() if result.analysed_at else None,
        "overall_tumour_pct": agg.overall_tumour_pct if agg else None,
        "overall_stroma_pct": agg.overall_stroma_pct if agg else None,
        "overall_necrosis_pct": agg.overall_necrosis_pct if agg else None,
        "hotspot_tumour_pct": agg.hotspot_tumour_pct if agg else None,
        "total_cells_analysed": agg.total_cells_analysed if agg else 0,
    }


def _ser_aggregates(agg) -> dict:
    return {
        "overall_tumour_pct": agg.overall_tumour_pct,
        "overall_stroma_pct": agg.overall_stroma_pct,
        "overall_necrosis_pct": agg.overall_necrosis_pct,
        "hotspot_tumour_pct": agg.hotspot_tumour_pct,
        "hotspot_row": agg.hotspot_row,
        "hotspot_col": agg.hotspot_col,
        "total_cells_analysed": agg.total_cells_analysed,
        "tissue_cells": agg.tissue_cells,
    }


def _ser_band(b) -> dict:
    return {
        "label": b.label,
        "min_pct": b.min_pct,
        "max_pct": b.max_pct,
        "cell_count": b.cell_count,
        "area_um2": b.area_um2,
    }


def _ser_cell(c) -> dict:
    return {
        "row": c.row,
        "col": c.col,
        "tumour_pct": c.tumour_pct,
        "stroma_pct": c.stroma_pct,
        "necrosis_pct": c.necrosis_pct,
        "inflammation_pct": c.inflammation_pct,
        "other_pct": c.other_pct,
    }


# ── Routes ────────────────────────────────────────────────────────────────


@router.get("/{slide_key}", summary="Full TCA analysis result")
async def get_tca_analysis(
    slide_key: str,
    svc: TCAAnalysisService = Depends(_tca_analysis_svc),
    overlay: TCAOverlayService = Depends(_overlay_svc),
):
    """
    Return the full TCA analysis for *slide_key* (filename stem or full name).

    If the TCA overlay assets have not been downloaded yet this returns
    ``{"available": false}`` — the client should fetch
    ``/api/overlay-config/{slide_name}`` first to trigger the download.
    """
    try:
        result = await svc.get_analysis(slide_key, overlay)
    except Exception as exc:
        logger.exception("TCA analysis failed for %s: %s", slide_key, exc)
        raise HTTPException(status_code=500, detail="TCA analysis error")

    return JSONResponse(_ser_result(result))


@router.get("/{slide_key}/summary", summary="Lightweight TCA aggregate summary")
async def get_tca_summary(
    slide_key: str,
    svc: TCAAnalysisService = Depends(_tca_analysis_svc),
    overlay: TCAOverlayService = Depends(_overlay_svc),
):
    """
    Return overall tumour/stroma/necrosis % and hotspot only.
    Cheaper than the full result when the grid is large.
    """
    try:
        result = await svc.get_analysis(slide_key, overlay)
    except Exception as exc:
        logger.exception("TCA summary failed for %s: %s", slide_key, exc)
        raise HTTPException(status_code=500, detail="TCA analysis error")

    return JSONResponse(_ser_summary(result))


@router.get("/{slide_key}/grid", summary="TCA spatial grid cells")
async def get_tca_grid(
    slide_key: str,
    svc: TCAAnalysisService = Depends(_tca_analysis_svc),
    overlay: TCAOverlayService = Depends(_overlay_svc),
):
    """
    Return the grid-cell array used for heatmap rendering.
    Returns an empty list if no grid data is available.
    """
    try:
        result = await svc.get_analysis(slide_key, overlay)
    except Exception as exc:
        logger.exception("TCA grid failed for %s: %s", slide_key, exc)
        raise HTTPException(status_code=500, detail="TCA analysis error")

    return JSONResponse({
        "slide_key": result.slide_key,
        "available": result.available,
        "grid_cells": [_ser_cell(c) for c in result.grid_cells],
        "total": len(result.grid_cells),
    })


@router.delete("/{slide_key}/cache", status_code=204, summary="Invalidate TCA cache")
async def invalidate_tca_cache(
    slide_key: str,
    svc: TCAAnalysisService = Depends(_tca_analysis_svc),
):
    """
    Remove the cached TCA analysis so the next GET re-parses from disk.
    Useful after a TCA pipeline re-run produces new overlay assets.
    """
    svc.invalidate(slide_key)
