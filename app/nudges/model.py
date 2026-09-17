import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class PatientNudgeType(str, enum.Enum):
    DRINK_WATER = "DRINK_WATER"
    TAKE_MEDICATION = "TAKE_MEDICATION"
    CHECK_BLOOD_PRESSURE = "CHECK_BLOOD_PRESSURE"


class PatientNudge(Base):
    __tablename__ = "patient_nudges"
    __table_args__ = (
        Index("ix_patient_nudges_patient_created", "patient_id", "created_at"),
    )

    nudge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("elderly_patients.patient_id"), nullable=False
    )
    sent_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    nudge_type: Mapped[PatientNudgeType] = mapped_column(
        Enum(PatientNudgeType, name="patient_nudge_type"), nullable=False
    )
    client_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
