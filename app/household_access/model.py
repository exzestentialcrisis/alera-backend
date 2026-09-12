import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class CaregiverPatientAssignment(Base):
    __tablename__ = "caregiver_patient_assignments"
    __table_args__ = (
        Index(
            "uq_caregiver_patient_assignments_active",
            "caregiver_user_id",
            "patient_id",
            unique=True,
            postgresql_where=text("unassigned_at IS NULL"),
        ),
        Index(
            "ix_caregiver_patient_assignments_patient_active",
            "patient_id",
            postgresql_where=text("unassigned_at IS NULL"),
        ),
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    caregiver_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("elderly_patients.patient_id"), nullable=False
    )
    assigned_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    unassigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PatientAccessCode(Base):
    __tablename__ = "patient_access_codes"
    __table_args__ = (
        Index("ix_patient_access_codes_patient_created", "patient_id", "created_at"),
        Index(
            "ix_patient_access_codes_active_selector",
            "access_code_selector",
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    access_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("elderly_patients.patient_id"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    access_code_selector: Mapped[str | None] = mapped_column(String(4), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def is_valid(self, *, at: datetime | None = None) -> bool:
        now = at or utc_now()
        return self.used_at is None and self.revoked_at is None and self.expires_at > now

    @property
    def status(self) -> str:
        if self.revoked_at is not None:
            return "REVOKED"
        if self.used_at is not None:
            return "USED"
        if self.expires_at <= utc_now():
            return "EXPIRED"
        return "VALID"
