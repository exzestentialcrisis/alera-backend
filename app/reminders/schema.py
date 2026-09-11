from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.reminders.enums import (
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
)


class ReminderOccurrenceRead(BaseModel):
    reminder_occurrence_id: UUID
    reminder_template_id: UUID
    patient_id: UUID
    title: str
    instructions: str | None
    category: ReminderCategory
    priority: ReminderPriority
    scheduled_at: datetime
    due_at: datetime
    status: ReminderOccurrenceStatus
    snooze_allowed: bool
    default_snooze_minutes: int
    missed_after_minutes: int


class ReminderOccurrenceListResponse(BaseModel):
    items: list[ReminderOccurrenceRead]
    total: int
    limit: int
    offset: int
