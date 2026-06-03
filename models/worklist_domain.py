"""
models/worklist_domain.py
Domain models for the pathology review Worklist.

Design intent
─────────────
A WorklistItem represents one pathology case queued for review.  It maps
1-to-1 to a WSI viewer session (patient_id + event_id + selected_slide_id).

The component internals (UI, workflow rules, assignment logic) are to be
defined in a later design iteration.  These models define the minimal data
contract so the service and routes can be scaffolded cleanly.

Status transitions (provisional)
─────────────────────────────────
  PENDING → IN_REVIEW → COMPLETE
                      ↘ FLAGGED
Any state → CANCELLED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ── Enumerations ──────────────────────────────────────────────────────────

WORKLIST_STATUS = frozenset(
    {"PENDING", "IN_REVIEW", "COMPLETE", "FLAGGED", "CANCELLED"}
)

WORKLIST_PRIORITY_MIN = 1   # urgent
WORKLIST_PRIORITY_MAX = 5   # routine


# ── Domain models ─────────────────────────────────────────────────────────

@dataclass
class WorklistItem:
    """
    A single case on the pathology review worklist.

    id               Opaque UUID (server-generated).
    patient_id       Patient identifier — passed through to the WSI viewer.
    event_id         Encounter / event identifier.
    selected_slide_id  The slide block ID to open in the viewer.
    status           One of WORKLIST_STATUS.
    priority         1 (urgent) – 5 (routine).
    assigned_to      Username / email of the reviewing pathologist (optional).
    notes            Free-text notes for handoff or flagging.
    created_at       UTC timestamp when the item was added.
    updated_at       UTC timestamp of the most recent modification.
    """
    id: str
    patient_id: str
    event_id: str
    selected_slide_id: str
    status: str                         # see WORKLIST_STATUS
    priority: int                       # 1–5
    assigned_to: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def viewer_params(self) -> dict:
        """Return the three standard query params for the WSI viewer."""
        return {
            "patient_id": self.patient_id,
            "event_id": self.event_id,
            "selected_slide_id": self.selected_slide_id,
        }


@dataclass
class Worklist:
    """Paginated collection of WorklistItems."""
    items: list[WorklistItem] = field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0
