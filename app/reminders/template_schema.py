from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.reminders.enums import (
    ReminderCategory,
    ReminderNotificationChannel,
    ReminderPriority,
    ReminderTemplateStatus,
)
from app.reminders.scheduling import normalize_schedule_rule


TITLE_MAX_LENGTH = 255
INSTRUCTIONS_MAX_LENGTH = 5000
TIMEZONE_MAX_LENGTH = 100


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class _ReminderTemplateFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    category: ReminderCategory
    instructions: str | None = Field(default=None, max_length=INSTRUCTIONS_MAX_LENGTH)
    priority: ReminderPriority = ReminderPriority.NORMAL
    start_date: date
    start_time: time
    timezone: str = Field(min_length=1, max_length=TIMEZONE_MAX_LENGTH)
    schedule_rule: str | None = None
    due_after_minutes: int = Field(default=15, ge=0, le=10080)
    snooze_allowed: bool = True
    default_snooze_minutes: int = Field(default=10, ge=0, le=1440)
    missed_after_minutes: int = Field(default=30, ge=0, le=10080)
    notification_channels: ReminderNotificationChannel = (
        ReminderNotificationChannel.IN_APP
    )

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("start_time")
    @classmethod
    def require_local_time(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("start_time must not include timezone information")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("schedule_rule")
    @classmethod
    def normalize_schedule(cls, value: str | None) -> str | None:
        try:
            return normalize_schedule_rule(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @model_validator(mode="after")
    def validate_snooze_configuration(self):
        if self.snooze_allowed and self.default_snooze_minutes < 1:
            raise ValueError(
                "default_snooze_minutes must be at least 1 when snooze is allowed"
            )
        return self


class ReminderTemplateCreate(_ReminderTemplateFields):
    patient_id: UUID


class ReminderTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_LENGTH)
    category: ReminderCategory | None = None
    instructions: str | None = Field(default=None, max_length=INSTRUCTIONS_MAX_LENGTH)
    priority: ReminderPriority | None = None
    start_date: date | None = None
    start_time: time | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=TIMEZONE_MAX_LENGTH)
    schedule_rule: str | None = None
    due_after_minutes: int | None = Field(default=None, ge=0, le=10080)
    snooze_allowed: bool | None = None
    default_snooze_minutes: int | None = Field(default=None, ge=0, le=1440)
    missed_after_minutes: int | None = Field(default=None, ge=0, le=10080)
    notification_channels: ReminderNotificationChannel | None = None
    status: ReminderTemplateStatus | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("start_time")
    @classmethod
    def require_local_time(cls, value: time | None) -> time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("start_time must not include timezone information")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("schedule_rule")
    @classmethod
    def normalize_schedule(cls, value: str | None) -> str | None:
        try:
            return normalize_schedule_rule(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("status")
    @classmethod
    def reject_archived_status(
        cls, value: ReminderTemplateStatus | None
    ) -> ReminderTemplateStatus | None:
        if value is ReminderTemplateStatus.ARCHIVED:
            raise ValueError("use the archive endpoint to archive a reminder template")
        return value


class ReminderTemplateRead(BaseModel):
    reminder_template_id: UUID
    patient_id: UUID
    created_by_user_id: UUID
    title: str
    category: ReminderCategory
    instructions: str | None
    priority: ReminderPriority
    start_date: date
    start_time: time
    timezone: str
    schedule_rule: str | None
    due_after_minutes: int
    snooze_allowed: bool
    default_snooze_minutes: int
    missed_after_minutes: int
    notification_channels: ReminderNotificationChannel
    status: ReminderTemplateStatus
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ReminderTemplateListResponse(BaseModel):
    items: list[ReminderTemplateRead]
    total: int
    limit: int
    offset: int
