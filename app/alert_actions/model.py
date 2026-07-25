import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.alerts.model import AlertStatus
from app.core.time import utc_now
from app.db.base import Base


class AlertActionType(str, enum.Enum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    RESOLVE = "RESOLVE"
    ESCALATE = "ESCALATE"
    MARK_FALSE_ALARM = "MARK_FALSE_ALARM"
    ADD_NOTE = "ADD_NOTE"
    LOG_INTERVENTION = "LOG_INTERVENTION"


class AlertAction(Base):
    __tablename__ = "alert_actions"
    __table_args__ = (
        Index(
            "ix_alert_actions_alert_performed_at",
            "alert_id",
            "performed_at",
        ),
    )

    alert_action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alerts.alert_id"),
        nullable=False,
    )
    performed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=True,
    )
    action_type: Mapped[AlertActionType] = mapped_column(
        Enum(AlertActionType, name="alert_action_type"),
        nullable=False,
    )
    action_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_status: Mapped[AlertStatus | None] = mapped_column(
        Enum(AlertStatus, name="alert_status", create_type=False),
        nullable=True,
    )
    new_status: Mapped[AlertStatus | None] = mapped_column(
        Enum(AlertStatus, name="alert_status", create_type=False),
        nullable=True,
    )
    action_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    performed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
