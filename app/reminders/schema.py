from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
)


REMINDER_ACTION_NOTE_MAX_LENGTH = 1000


class ReminderActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_action_id: UUID
    note: str | None = None

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > REMINDER_ACTION_NOTE_MAX_LENGTH:
            raise ValueError(
                f"note must not exceed {REMINDER_ACTION_NOTE_MAX_LENGTH} characters"
            )
        return value or None


class ReminderCompleteRequest(ReminderActionRequest):
    pass


class ReminderSnoozeRequest(ReminderActionRequest):
    snooze_minutes: int | None = Field(default=None, ge=1, le=1440)


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


class ReminderActionRead(BaseModel):
    reminder_action_id: UUID
    client_action_id: UUID
    reminder_occurrence_id: UUID
    performed_by_user_id: UUID
    action_type: ReminderActionType
    action_note: str | None
    previous_status: ReminderOccurrenceStatus | None
    new_status: ReminderOccurrenceStatus | None
    new_due_at: datetime | None
    metadata: dict
    performed_at: datetime


class ReminderActionResponse(BaseModel):
    reminder: ReminderOccurrenceRead
    action: ReminderActionRead
    idempotent: bool
