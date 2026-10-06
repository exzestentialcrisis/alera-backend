import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class HelpRequestStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class HelpRequest(Base):
    __tablename__ = "help_requests"
    __table_args__ = (
        Index(
            "ix_help_requests_patient_requested",
            "patient_id",
            "requested_at",
        ),
        Index(
            "uq_help_requests_patient_unresolved",
            "patient_id",
            unique=True,
            postgresql_where=text(
                "status IN ('PENDING'::help_request_status, "
                "'ACKNOWLEDGED'::help_request_status)"
            ),
        ),
    )

    help_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("elderly_patients.patient_id"),
        nullable=False,
    )
    status: Mapped[HelpRequestStatus] = mapped_column(
        Enum(HelpRequestStatus, name="help_request_status"),
        nullable=False,
        default=HelpRequestStatus.PENDING,
        server_default="PENDING",
    )
    message: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    client_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class HelpRequestNote(Base):
    __tablename__ = "help_request_notes"
    __table_args__ = (
        Index(
            "ix_help_request_notes_request_created",
            "help_request_id",
            "created_at",
        ),
    )

    help_request_note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    help_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "help_requests.help_request_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
    )
    client_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )
    note: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
