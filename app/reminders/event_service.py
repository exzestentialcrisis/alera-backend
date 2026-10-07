"""Append-only reminder occurrence timeline events."""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.reminders.enums import (
    ReminderEventActorRole,
    ReminderOccurrenceEventType,
)
from app.reminders.model import ReminderOccurrenceEvent
from app.users.model import User, UserRole


def reminder_event_actor_role(actor: User | None) -> ReminderEventActorRole:
    """Map an authenticated actor to the public timeline role."""
    if actor is None:
        return ReminderEventActorRole.SYSTEM
    if actor.role is UserRole.ELDERLY_PATIENT:
        return ReminderEventActorRole.PATIENT
    if actor.role in {UserRole.CAREGIVER, UserRole.CARE_ADMIN}:
        return ReminderEventActorRole.CAREGIVER
    raise ValueError("Unsupported reminder event actor role.")


def record_reminder_occurrence_event(
    db: Session,
    *,
    occurrence_id,
    event_type: ReminderOccurrenceEventType,
    actor: User | None = None,
    occurred_at: datetime | None = None,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ReminderOccurrenceEvent:
    """Append an event without committing the surrounding transaction."""
    event = ReminderOccurrenceEvent(
        reminder_occurrence_id=occurrence_id,
        event_type=event_type,
        occurred_at=occurred_at or utc_now(),
        actor_user_id=actor.user_id if actor is not None else None,
        actor_role=reminder_event_actor_role(actor),
        note=note,
        event_metadata=dict(metadata or {}),
    )
    db.add(event)
    return event
