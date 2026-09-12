import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base
from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderNotificationChannel,
    ReminderOccurrenceStatus,
    ReminderPriority,
    ReminderTemplateStatus,
)


class ReminderTemplate(Base):
    __tablename__ = "reminder_templates"
    __table_args__ = (
        CheckConstraint(
            "default_snooze_minutes >= 0",
            name="reminder_default_snooze_nonnegative",
        ),
        CheckConstraint(
            "missed_after_minutes >= 0",
            name="reminder_missed_after_nonnegative",
        ),
        Index("idx_reminder_templates_created_by", "created_by_user_id"),
        Index("idx_reminder_templates_patient_id", "patient_id"),
        Index("idx_reminder_templates_status", "status"),
    )

    reminder_template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("elderly_patients.patient_id"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[ReminderCategory] = mapped_column(
        ENUM(ReminderCategory, name="reminder_category_enum", create_type=False),
        nullable=False,
    )
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[ReminderPriority] = mapped_column(
        ENUM(ReminderPriority, name="reminder_priority_enum", create_type=False),
        nullable=False,
        default=ReminderPriority.NORMAL,
        server_default="NORMAL",
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    schedule_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    snooze_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    default_snooze_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=10, server_default="10"
    )
    missed_after_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=30, server_default="30"
    )
    notification_channels: Mapped[ReminderNotificationChannel] = mapped_column(
        ENUM(
            ReminderNotificationChannel,
            name="reminder_notification_channel_enum",
            create_type=False,
        ),
        nullable=False,
        default=ReminderNotificationChannel.IN_APP,
        server_default="IN_APP",
    )
    status: Mapped[ReminderTemplateStatus] = mapped_column(
        ENUM(
            ReminderTemplateStatus,
            name="reminder_template_status_enum",
            create_type=False,
        ),
        nullable=False,
        default=ReminderTemplateStatus.ACTIVE,
        server_default="ACTIVE",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default="now()",
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReminderOccurrence(Base):
    __tablename__ = "reminder_occurrences"
    __table_args__ = (
        Index("idx_reminder_occurrences_due_at", "due_at"),
        Index("idx_reminder_occurrences_status", "status"),
        Index("idx_reminder_occurrences_template_id", "reminder_template_id"),
    )

    reminder_occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reminder_template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reminder_templates.reminder_template_id"),
        nullable=False,
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ReminderOccurrenceStatus] = mapped_column(
        ENUM(
            ReminderOccurrenceStatus,
            name="reminder_occurrence_status_enum",
            create_type=False,
        ),
        nullable=False,
        default=ReminderOccurrenceStatus.UPCOMING,
        server_default="UPCOMING",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default="now()",
    )


class ReminderAction(Base):
    __tablename__ = "reminder_actions"
    __table_args__ = (
        Index("idx_reminder_actions_occurrence_id", "reminder_occurrence_id"),
        Index("idx_reminder_actions_performed_by", "performed_by_user_id"),
        Index(
            "uq_reminder_actions_client_action_id",
            "client_action_id",
            unique=True,
        ),
    )

    reminder_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reminder_occurrence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reminder_occurrences.reminder_occurrence_id"),
        nullable=False,
    )
    performed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    client_action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    action_type: Mapped[ReminderActionType] = mapped_column(
        ENUM(ReminderActionType, name="reminder_action_type_enum", create_type=False),
        nullable=False,
    )
    action_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_status: Mapped[ReminderOccurrenceStatus | None] = mapped_column(
        ENUM(
            ReminderOccurrenceStatus,
            name="reminder_occurrence_status_enum",
            create_type=False,
        ),
        nullable=True,
    )
    new_status: Mapped[ReminderOccurrenceStatus | None] = mapped_column(
        ENUM(
            ReminderOccurrenceStatus,
            name="reminder_occurrence_status_enum",
            create_type=False,
        ),
        nullable=True,
    )
    new_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    action_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, server_default="now()"
    )
