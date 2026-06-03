"""
services/worklist_service.py
In-process worklist store for pathology case review.

Design
──────
• Manages a queue of WorklistItems representing pathology cases awaiting review.
• In-memory store (dict[str, WorklistItem]).  Lost on process restart — this
  is intentional for the initial scaffold.  A Redis / PostgreSQL / Firestore
  backend can be dropped in later by replacing the _store operations without
  changing any caller.

• Filtering: status, assigned_to.
• Pagination: limit + offset.
• Thread safety: asyncio single-event-loop — no locking needed.

WorklistItem lifecycle
──────────────────────
  add_item()   → status=PENDING
  update_item() → any status transition (validation is caller's responsibility
                  for now; a state-machine guard can be added later)
  delete_item() → hard-remove (not soft-delete)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from models.worklist_domain import WORKLIST_STATUS, Worklist, WorklistItem

logger = logging.getLogger(__name__)


# ── Errors ────────────────────────────────────────────────────────────────


class WorklistItemNotFoundError(KeyError):
    pass


class InvalidWorklistStatusError(ValueError):
    pass


class InvalidWorklistPriorityError(ValueError):
    pass


# ── Service ───────────────────────────────────────────────────────────────


class WorklistService:
    """
    CRUD store for pathology review worklist items.
    Singleton — wired onto app.state in main.py.
    """

    def __init__(self) -> None:
        self._store: dict[str, WorklistItem] = {}

    # ── Read ──────────────────────────────────────────────────────────────

    def list_items(
        self,
        status: Optional[str] = None,
        assigned_to: Optional[str] = None,
        priority: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Worklist:
        """
        Return a paginated Worklist, optionally filtered.

        Filters are ANDed.  Results are sorted by priority ASC then
        created_at ASC (most urgent / oldest first).
        """
        items = list(self._store.values())

        # Filter
        if status:
            items = [i for i in items if i.status == status]
        if assigned_to:
            items = [i for i in items if i.assigned_to == assigned_to]
        if priority is not None:
            items = [i for i in items if i.priority == priority]

        # Sort: priority ascending (1 = urgent first), then oldest first
        items.sort(key=lambda i: (i.priority, i.created_at))

        total = len(items)
        page = items[offset: offset + limit]

        return Worklist(items=page, total=total, limit=limit, offset=offset)

    def get_item(self, item_id: str) -> WorklistItem:
        """Return item by ID. Raises WorklistItemNotFoundError if absent."""
        item = self._store.get(item_id)
        if not item:
            raise WorklistItemNotFoundError(item_id)
        return item

    # ── Write ─────────────────────────────────────────────────────────────

    def add_item(
        self,
        patient_id: str,
        event_id: str,
        selected_slide_id: str,
        priority: int = 3,
        assigned_to: Optional[str] = None,
        notes: Optional[str] = None,
        item_id: Optional[str] = None,
    ) -> WorklistItem:
        """
        Add a new item with status=PENDING.

        priority must be 1–5 (1 = urgent, 5 = routine).
        item_id is auto-generated if omitted.
        """
        _validate_priority(priority)
        now = datetime.utcnow()
        ann_id = item_id or str(uuid.uuid4())

        item = WorklistItem(
            id=ann_id,
            patient_id=patient_id,
            event_id=event_id,
            selected_slide_id=selected_slide_id,
            status="PENDING",
            priority=priority,
            assigned_to=assigned_to,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        self._store[ann_id] = item
        logger.info(
            "WorklistService: added item %s  patient=%s  priority=%d",
            ann_id, patient_id, priority,
        )
        return item

    def update_item(
        self,
        item_id: str,
        status: Optional[str] = None,
        priority: Optional[int] = None,
        assigned_to: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> WorklistItem:
        """
        Partial update.  Only supplied (non-None) fields are changed.
        Raises WorklistItemNotFoundError / InvalidWorklistStatusError.
        """
        item = self.get_item(item_id)

        if status is not None:
            _validate_status(status)
            item.status = status
        if priority is not None:
            _validate_priority(priority)
            item.priority = priority
        if assigned_to is not None:
            item.assigned_to = assigned_to or None  # empty string → None
        if notes is not None:
            item.notes = notes or None

        item.updated_at = datetime.utcnow()
        logger.debug("WorklistService: updated item %s  status=%s", item_id, item.status)
        return item

    def delete_item(self, item_id: str) -> None:
        """Remove item permanently. Raises WorklistItemNotFoundError if absent."""
        if item_id not in self._store:
            raise WorklistItemNotFoundError(item_id)
        del self._store[item_id]
        logger.info("WorklistService: deleted item %s", item_id)

    # ── Bulk helpers ──────────────────────────────────────────────────────

    def count_by_status(self) -> dict[str, int]:
        """Return {status: count} summary across all items."""
        counts: dict[str, int] = {s: 0 for s in WORKLIST_STATUS}
        for item in self._store.values():
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts


# ── Validators ────────────────────────────────────────────────────────────


def _validate_status(status: str) -> None:
    if status not in WORKLIST_STATUS:
        raise InvalidWorklistStatusError(
            f"status={status!r} is not valid. Choose from: {sorted(WORKLIST_STATUS)}"
        )


def _validate_priority(priority: int) -> None:
    if not (1 <= priority <= 5):
        raise InvalidWorklistPriorityError(
            f"priority={priority} out of range (1–5)"
        )
