import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class MetricType(str, enum.Enum):
    HEART_RATE = "HEART_RATE"
    SPO2 = "SPO2"
    ACTIVITY = "ACTIVITY"
    INACTIVITY = "INACTIVITY"
    SLEEP = "SLEEP"
    BATTERY_LEVEL = "BATTERY_LEVEL"
    CONNECTION_STATUS = "CONNECTION_STATUS"
    SYNC_STATUS = "SYNC_STATUS"


class ValidationStatus(str, enum.Enum):
    VALID_REALTIME = "VALID_REALTIME"
    DELAYED_USABLE = "DELAYED_USABLE"
    INVALID = "INVALID"


class HealthEvent(Base):
    __tablename__ = "health_events"
    __table_args__ = (
        Index(
            "ix_health_events_patient_metric_recorded",
            "patient_id",
            "metric_type",
            "recorded_at",
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("elderly_patients.patient_id"),
        nullable=False,
    )

    external_event_id: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
        unique=True,
    )

    metric_type: Mapped[MetricType] = mapped_column(
        Enum(MetricType, name="metric_type"),
        nullable=False,
    )

    numeric_value: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    text_value: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    boolean_value: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    metric_unit: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    validation_status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="validation_status"),
        nullable=False,
    )

    validation_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    raw_payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
