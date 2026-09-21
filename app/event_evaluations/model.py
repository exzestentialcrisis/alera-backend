import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class ConditionKey(str, enum.Enum):
    HR_NORMAL = "HR_NORMAL"
    HR_HIGH = "HR_HIGH"
    HR_LOW = "HR_LOW"

    SPO2_NORMAL = "SPO2_NORMAL"
    SPO2_LOW = "SPO2_LOW"

    INACTIVITY = "INACTIVITY"
    NO_DATA = "NO_DATA"
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"
    BATTERY_LOW = "BATTERY_LOW"
    SYNC_FAILURE = "SYNC_FAILURE"

    PHONE_DISCONNECTED = "PHONE_DISCONNECTED"
    WATCH_DISCONNECTED = "WATCH_DISCONNECTED"
    WATCH_NOT_WORN = "WATCH_NOT_WORN"

    PHONE_BATTERY_LOW = "PHONE_BATTERY_LOW"
    WATCH_BATTERY_LOW = "WATCH_BATTERY_LOW"

class MonitoringState(str, enum.Enum):
    STABLE = "STABLE"
    ELEVATED = "ELEVATED"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    RESOLVED = "RESOLVED"
    UNKNOWN = "UNKNOWN"


class EvaluationSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class EventEvaluation(Base):
    __tablename__ = "event_evaluations"
    __table_args__ = (Index("ix_event_evaluations_alert_id", "alert_id"),)

    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("health_events.event_id"),
        nullable=False,
    )

    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alerts.alert_id"),
        nullable=True,
    )

    condition_key: Mapped[ConditionKey] = mapped_column(
        Enum(ConditionKey, name="condition_key"),
        nullable=False,
    )

    threshold_value_used: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    threshold_met: Mapped[bool] = mapped_column(Boolean, nullable=False)
    persistence_met: Mapped[bool] = mapped_column(Boolean, nullable=False)

    previous_state: Mapped[MonitoringState] = mapped_column(
        Enum(MonitoringState, name="monitoring_state"),
        nullable=False,
    )

    new_state: Mapped[MonitoringState] = mapped_column(
        Enum(
            MonitoringState,
            name="monitoring_state",
            create_type=False,
        ),
        nullable=False,
    )

    severity: Mapped[EvaluationSeverity] = mapped_column(
        Enum(EvaluationSeverity, name="evaluation_severity"),
        nullable=False,
    )

    evaluation_reason: Mapped[str] = mapped_column(Text, nullable=False)

    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
