"""
routes/annotation_routes.py
HTTP API for per-slide annotations.

Route map
─────────
GET    /api/annotations/{slide_key}                  → full SlideAnnotations
POST   /api/annotations/{slide_key}/polygons         → add polygon
PATCH  /api/annotations/{slide_key}/polygons/{id}    → update polygon
DELETE /api/annotations/{slide_key}/polygons/{id}    → delete polygon
POST   /api/annotations/{slide_key}/labels           → add label
PATCH  /api/annotations/{slide_key}/labels/{id}      → update label
DELETE /api/annotations/{slide_key}/labels/{id}      → delete label
POST   /api/annotations/{slide_key}/measures         → add measure
DELETE /api/annotations/{slide_key}/measures/{id}    → delete measure
DELETE /api/annotations/{slide_key}                  → clear all for slide

All routes require the standard three query params (patient_id, event_id,
selected_slide_id) so the frontend can reuse the same apiPath() helper.
The params are validated but only used for session-scoping; annotation
state lives in AnnotationService keyed by slide_key.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from services.annotation_service import (
    AnnotationNotFoundError,
    AnnotationService,
    DuplicateAnnotationError,
    InvalidAnnotationError,
)
from models.domain import (
    LabelAnnotation,
    MeasureAnnotation,
    PolygonAnnotation,
    SlideAnnotations,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/annotations", tags=["annotations"])


# ── Dependency ────────────────────────────────────────────────────────────

def _annotation_svc(request: Request) -> AnnotationService:
    return request.app.state.annotation_service


# ── Pydantic request / response bodies ───────────────────────────────────

class PointBody(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0, description="Viewport x (0–1)")
    y: float = Field(..., ge=0.0, le=1.0, description="Viewport y (0–1)")


class AddPolygonBody(BaseModel):
    vertices: list[PointBody] = Field(..., min_length=3)
    label: Optional[str] = None
    id: Optional[str] = None


class UpdatePolygonBody(BaseModel):
    vertices: Optional[list[PointBody]] = Field(default=None, min_length=3)
    label: Optional[str] = None


class AddLabelBody(BaseModel):
    anchor: PointBody
    offset: PointBody
    text: str = Field(..., min_length=1)
    id: Optional[str] = None


class UpdateLabelBody(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1)
    offset: Optional[PointBody] = None


class AddMeasureBody(BaseModel):
    start: PointBody
    end: PointBody
    slide_width_px: Optional[int] = None
    slide_height_px: Optional[int] = None
    mpp: Optional[float] = Field(default=None, gt=0)
    id: Optional[str] = None


# ── Serialisers ───────────────────────────────────────────────────────────

def _serialise_slide_annotations(sa: SlideAnnotations) -> dict:
    return {
        "slide_key": sa.slide_key,
        "polygons": [_ser_polygon(p) for p in sa.polygons],
        "labels": [_ser_label(l) for l in sa.labels],
        "measures": [_ser_measure(m) for m in sa.measures],
    }


def _ser_polygon(p: PolygonAnnotation) -> dict:
    return {
        "id": p.id,
        "vertices": [{"x": v.x, "y": v.y} for v in p.vertices],
        "label": p.label,
        "created_at": p.created_at.isoformat(),
    }


def _ser_label(l: LabelAnnotation) -> dict:
    return {
        "id": l.id,
        "anchor": {"x": l.anchor.x, "y": l.anchor.y},
        "offset": {"x": l.offset.x, "y": l.offset.y},
        "text": l.text,
        "created_at": l.created_at.isoformat(),
    }


def _ser_measure(m: MeasureAnnotation) -> dict:
    return {
        "id": m.id,
        "start": {"x": m.start.x, "y": m.start.y},
        "end": {"x": m.end.x, "y": m.end.y},
        "distance_um": m.distance_um,
        "distance_px": m.distance_px,
        "created_at": m.created_at.isoformat(),
    }


# ── Error → HTTP ──────────────────────────────────────────────────────────

def _handle_service_error(exc: Exception) -> None:
    if isinstance(exc, AnnotationNotFoundError):
        raise HTTPException(status_code=404, detail=f"Annotation not found: {exc}")
    if isinstance(exc, DuplicateAnnotationError):
        raise HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidAnnotationError):
        raise HTTPException(status_code=422, detail=str(exc))
    raise exc


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/{slide_key}")
async def get_annotations(
    slide_key: str,
    svc: AnnotationService = Depends(_annotation_svc),
):
    """Return all annotations for a slide."""
    sa = svc.get_slide_annotations(slide_key)
    return JSONResponse(_serialise_slide_annotations(sa))


@router.delete("/{slide_key}")
async def clear_slide(
    slide_key: str,
    svc: AnnotationService = Depends(_annotation_svc),
):
    """Clear all annotations for a slide (matches 'Clear all' toolbar button)."""
    svc.clear_slide(slide_key)
    return JSONResponse({"cleared": True, "slide_key": slide_key})


# ── Polygons ──────────────────────────────────────────────────────────────

@router.post("/{slide_key}/polygons", status_code=201)
async def add_polygon(
    slide_key: str,
    body: AddPolygonBody,
    svc: AnnotationService = Depends(_annotation_svc),
):
    try:
        poly = svc.add_polygon(
            slide_key=slide_key,
            vertices=[v.model_dump() for v in body.vertices],
            label=body.label,
            annotation_id=body.id,
        )
    except Exception as exc:
        _handle_service_error(exc)
    return JSONResponse(_ser_polygon(poly), status_code=201)


@router.patch("/{slide_key}/polygons/{annotation_id}")
async def update_polygon(
    slide_key: str,
    annotation_id: str,
    body: UpdatePolygonBody,
    svc: AnnotationService = Depends(_annotation_svc),
):
    try:
        poly = svc.update_polygon(
            slide_key=slide_key,
            annotation_id=annotation_id,
            vertices=[v.model_dump() for v in body.vertices] if body.vertices is not None else None,
            label=body.label,
        )
    except Exception as exc:
        _handle_service_error(exc)
    return JSONResponse(_ser_polygon(poly))


@router.delete("/{slide_key}/polygons/{annotation_id}", status_code=204)
async def delete_polygon(
    slide_key: str,
    annotation_id: str,
    svc: AnnotationService = Depends(_annotation_svc),
):
    try:
        svc.delete_polygon(slide_key, annotation_id)
    except Exception as exc:
        _handle_service_error(exc)


# ── Labels ────────────────────────────────────────────────────────────────

@router.post("/{slide_key}/labels", status_code=201)
async def add_label(
    slide_key: str,
    body: AddLabelBody,
    svc: AnnotationService = Depends(_annotation_svc),
):
    try:
        lbl = svc.add_label(
            slide_key=slide_key,
            anchor=body.anchor.model_dump(),
            offset=body.offset.model_dump(),
            text=body.text,
            annotation_id=body.id,
        )
    except Exception as exc:
        _handle_service_error(exc)
    return JSONResponse(_ser_label(lbl), status_code=201)


@router.patch("/{slide_key}/labels/{annotation_id}")
async def update_label(
    slide_key: str,
    annotation_id: str,
    body: UpdateLabelBody,
    svc: AnnotationService = Depends(_annotation_svc),
):
    try:
        lbl = svc.update_label(
            slide_key=slide_key,
            annotation_id=annotation_id,
            text=body.text,
            offset=body.offset.model_dump() if body.offset else None,
        )
    except Exception as exc:
        _handle_service_error(exc)
    return JSONResponse(_ser_label(lbl))


@router.delete("/{slide_key}/labels/{annotation_id}", status_code=204)
async def delete_label(
    slide_key: str,
    annotation_id: str,
    svc: AnnotationService = Depends(_annotation_svc),
):
    try:
        svc.delete_label(slide_key, annotation_id)
    except Exception as exc:
        _handle_service_error(exc)


# ── Measures ──────────────────────────────────────────────────────────────

@router.post("/{slide_key}/measures", status_code=201)
async def add_measure(
    slide_key: str,
    body: AddMeasureBody,
    svc: AnnotationService = Depends(_annotation_svc),
):
    try:
        m = svc.add_measure(
            slide_key=slide_key,
            start=body.start.model_dump(),
            end=body.end.model_dump(),
            slide_width_px=body.slide_width_px,
            slide_height_px=body.slide_height_px,
            mpp=body.mpp,
            annotation_id=body.id,
        )
    except Exception as exc:
        _handle_service_error(exc)
    return JSONResponse(_ser_measure(m), status_code=201)


@router.delete("/{slide_key}/measures/{annotation_id}", status_code=204)
async def delete_measure(
    slide_key: str,
    annotation_id: str,
    svc: AnnotationService = Depends(_annotation_svc),
):
    try:
        svc.delete_measure(slide_key, annotation_id)
    except Exception as exc:
        _handle_service_error(exc)
