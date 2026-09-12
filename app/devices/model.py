import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class CaregiverPushDevice(Base):
    __tablename__ = "caregiver_push_devices"
    __table_args__ = (
        CheckConstraint("platform = 'ANDROID'", name="ck_push_devices_platform"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), index=True
    )
    fcm_token: Mapped[str] = mapped_column(String(2048), unique=True)
    platform: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
