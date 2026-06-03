"""
routes/worklist_routes.py
HTTP API for the pathology review Worklist.

Route map
─────────
GET    /api/worklist                → list items (filter + paginate)
POST   /api/worklist                → add item
GET    /api/worklist/stats          → status count summary
GET    /api/worklist/{item_id}      → single item
PATCH  /api/worklist/{item_id}      → update status / priority / assignee / notes
DELETE /api/worklist/{item_id}      → remove item

Design note
───────────
The Worklist UI component is to be defined in a later iteration.
These routes follow the same thin-route pattern as annotation_routes.py:
  1. Validate inputs via Pydantic request bodies.
  2. Delegate to WorklistService.
  3. Serialise domain objects → JSON.

No business logic lives here.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from services.worklist_service import (
    InvalidWorklistPriorityError,
    InvalidWorklistStatusError,
    WorklistItemNotFoundError,
    WorklistService,
)
from models.worklist_domain import WORKLIST_STATUS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/worklist", tags=["worklist"])


# ── Dependency ────────────────────────────────────────────────────────────


def _worklist_svc(request: Request) -> WorklistService:
    return request.app.state.worklist_service


# ── Pydantic request bodies ───────────────────────────────────────────────


class AddWorklistItemBody(BaseModel):
    patient_id: str = Field(..., min_length=1)
    event_id: str = Field(..., min_length=1)
    selected_slide_id: str = Field(..., min_length=1)
    priority: int = Field(default=3, ge=1, le=5, description="1=urgent … 5=routine")
    assigned_to: Optional[str] = None
    notes: Optional[str] = None


class UpdateWorklistItemBody(BaseModel):
    status: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=1, le=5)
    assigned_to: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in WORKLIST_STATUS:
            raise ValueError(f"status must be one of {sorted(WORKLIST_STATUS)}")
        return v


# ── Serialisers ───────────────────────────────────────────────────────────


def _ser_item(item) -> dict:
    return {
        "id": item.id,
        "patient_id": item.patient_id,
        "event_id": item.event_id,
        "selected_slide_id": item.selected_slide_id,
        "status": item.status,
        "priority": item.priority,
        "assigned_to": item.assigned_to,
        "notes": item.notes,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "viewer_params": item.viewer_params(),
    }


def _ser_worklist(wl) -> dict:
    return {
        "items": [_ser_item(i) for i in wl.items],
        "total": wl.total,
        "limit": wl.limit,
        "offset": wl.offset,
    }


# ── Error handler ─────────────────────────────────────────────────────────


def _handle_error(exc: Exception) -> None:
    if isinstance(exc, WorklistItemNotFoundError):
        raise HTTPException(status_code=404, detail=f"Worklist item not found: {exc}")
    if isinstance(exc, (InvalidWorklistStatusError, InvalidWorklistPriorityError)):
        raise HTTPException(status_code=422, detail=str(exc))
    raise exc


# ── Routes ────────────────────────────────────────────────────────────────


@router.get("", summary="List worklist items")
async def list_worklist(
    status: Optional[str] = Query(default=None, description="Filter by status"),
    assigned_to: Optional[str] = Query(default=None, description="Filter by assignee"),
    priority: Optional[int] = Query(default=None, ge=1, le=5, description="Filter by priority"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    svc: WorklistService = Depends(_worklist_svc),
):
    """
    Return a paginated list of worklist items.
    Filter by ``status``, ``assigned_to``, and/or ``priority``.
    Sorted by priority ascending then created_at ascending.
    """
    wl = svc.list_items(
        status=status,
        assigned_to=assigned_to,
        priority=priority,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(_ser_worklist(wl))


@router.post("", status_code=201, summary="Add item to worklist")
async def add_worklist_item(
    body: AddWorklistItemBody,
    svc: WorklistService = Depends(_worklist_svc),
):
    """
    Add a new pathology case to the worklist with status=PENDING.
    The returned item includes ``viewer_params`` — pass these directly
    to the WSI viewer URL query string.
    """
    try:
        item = svc.add_item(
            patient_id=body.patient_id,
            event_id=body.event_id,
            selected_slide_id=body.selected_slide_id,
            priority=body.priority,
            assigned_to=body.assigned_to,
            notes=body.notes,
        )
    except Exception as exc:
        _handle_error(exc)
    return JSONResponse(_ser_item(item), status_code=201)


@router.get("/stats", summary="Worklist status counts")
async def worklist_stats(
    svc: WorklistService = Depends(_worklist_svc),
):
    """
    Return a {status: count} summary across all worklist items.
    Useful for dashboard badges / KPI tiles.
    """
    return JSONResponse({"counts": svc.count_by_status()})


@router.get("/{item_id}", summary="Get single worklist item")
async def get_worklist_item(
    item_id: str,
    svc: WorklistService = Depends(_worklist_svc),
):
    """Return a single worklist item by ID."""
    try:
        item = svc.get_item(item_id)
    except Exception as exc:
        _handle_error(exc)
    return JSONResponse(_ser_item(item))


@router.patch("/{item_id}", summary="Update worklist item")
async def update_worklist_item(
    item_id: str,
    body: UpdateWorklistItemBody,
    svc: WorklistService = Depends(_worklist_svc),
):
    """
    Partially update a worklist item.
    Only supplied (non-null) fields are modified.
    Pass ``assigned_to: ""`` to clear the assignee.
    """
    try:
        item = svc.update_item(
            item_id=item_id,
            status=body.status,
            priority=body.priority,
            assigned_to=body.assigned_to,
            notes=body.notes,
        )
    except Exception as exc:
        _handle_error(exc)
    return JSONResponse(_ser_item(item))


@router.delete("/{item_id}", status_code=204, summary="Remove worklist item")
async def delete_worklist_item(
    item_id: str,
    svc: WorklistService = Depends(_worklist_svc),
):
    """Permanently remove an item from the worklist."""
    try:
        svc.delete_item(item_id)
    except Exception as exc:
        _handle_error(exc)
