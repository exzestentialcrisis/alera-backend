import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base
from app.event_evaluations.model import ConditionKey, EvaluationSeverity


class AlertStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    FALSE_ALARM = "FALSE_ALARM"
    ARCHIVED = "ARCHIVED"


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(
            "confirmed_at >= detected_at",
            name="ck_alerts_confirmed_at_after_detected_at",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= confirmed_at",
            name="ck_alerts_resolved_at_after_confirmed_at",
        ),
        Index("ix_alerts_patient_status", "patient_id", "status"),
        Index(
            "ix_alerts_patient_condition_detected",
            "patient_id",
            "condition_key",
            "detected_at",
        ),
        Index("ix_alerts_detected_at", "detected_at"),
        Index(
            "uq_alerts_unresolved_patient_condition_occurrence",
            "patient_id",
            "condition_key",
            "detected_at",
            unique=True,
            postgresql_where=text(
                "status IN ('ACTIVE'::alert_status, "
                "'ACKNOWLEDGED'::alert_status)"
            ),
        ),
    )

    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("elderly_patients.patient_id"),
        nullable=False,
    )
    condition_key: Mapped[ConditionKey] = mapped_column(
        Enum(ConditionKey, name="condition_key", create_type=False),
        nullable=False,
    )
    severity: Mapped[EvaluationSeverity] = mapped_column(
        Enum(
            EvaluationSeverity,
            name="evaluation_severity",
            create_type=False,
        ),
        nullable=False,
    )
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, name="alert_status"),
        nullable=False,
        default=AlertStatus.ACTIVE,
        server_default="ACTIVE",
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
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
