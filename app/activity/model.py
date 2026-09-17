import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class ActivityType(str, enum.Enum):
    STEPS = "STEPS"
    WALKING = "WALKING"
    RUNNING = "RUNNING"
    EXERCISE = "EXERCISE"
    SLEEP = "SLEEP"


class SleepType(str, enum.Enum):
    MAIN = "MAIN"
    NAP = "NAP"
    UNKNOWN = "UNKNOWN"


class ActivityData(Base):
    """
    Parent record.

    One row represents one activity type for one patient on one calendar day.
    """

    __tablename__ = "activity_data"

    __table_args__ = (
        UniqueConstraint(
            "patient_id",
            "activity_date",
            "activity_type",
            name="uq_activity_data_patient_date_type",
        ),
        Index(
            "ix_activity_data_patient_date",
            "patient_id",
            "activity_date",
        ),
    )

    activity_data_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "elderly_patients.patient_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    activity_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    activity_type: Mapped[ActivityType] = mapped_column(
        Enum(
            ActivityType,
            name="activity_type",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class ActivityDailyData(Base):
    """
    One-to-one daily summary belonging to ActivityData.
    """

    __tablename__ = "activity_daily_data"

    __table_args__ = (
        CheckConstraint(
            "total_steps IS NULL OR total_steps >= 0",
            name="ck_activity_daily_steps_nonnegative",
        ),
        CheckConstraint(
            "total_duration_seconds >= 0",
            name="ck_activity_daily_duration_nonnegative",
        ),
        CheckConstraint(
            "session_count >= 0",
            name="ck_activity_daily_session_count_nonnegative",
        ),
        Index(
            "ix_activity_daily_last_movement",
            "last_movement_at",
        ),
    )

    activity_daily_data_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    activity_data_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "activity_data.activity_data_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
    )

    total_steps: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    total_duration_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    total_distance_meters: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    session_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    first_movement_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_movement_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class ActivitySession(Base):
    """
    Individual activity/sleep sessions belonging to ActivityData.

    ended_at = NULL means the session is still active.
    That is especially useful for current sleep detection.
    """

    __tablename__ = "activity_sessions"

    __table_args__ = (
        UniqueConstraint(
            "activity_data_id",
            "external_session_id",
            name="uq_activity_sessions_external_session",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_activity_sessions_end_after_start",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_activity_sessions_duration_nonnegative",
        ),
        CheckConstraint(
            "steps IS NULL OR steps >= 0",
            name="ck_activity_sessions_steps_nonnegative",
        ),
        Index(
            "ix_activity_sessions_activity_started",
            "activity_data_id",
            "started_at",
        ),
    )

    activity_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    activity_data_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "activity_data.activity_data_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    external_session_id: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    sleep_type: Mapped[SleepType | None] = mapped_column(
        Enum(
            SleepType,
            name="sleep_type",
        ),
        nullable=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    duration_seconds: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    steps: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    distance_meters: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    source: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )