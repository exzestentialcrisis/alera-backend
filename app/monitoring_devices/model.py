import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.base import Base


class MonitoringDeviceType(str, enum.Enum):
    WATCH = "WATCH"
    PHONE = "PHONE"


class DeviceConnectionStatus(str, enum.Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    UNKNOWN = "UNKNOWN"

class MonitoringDevice(Base):
    __tablename__ = "monitoring_devices"

    __table_args__ = (
        UniqueConstraint(
            "patient_id",
            "device_type",
            name="uq_monitoring_devices_patient_type",
        ),
        CheckConstraint(
            "battery_percent IS NULL "
            "OR battery_percent BETWEEN 0 AND 100",
            name="ck_monitoring_devices_battery_range",
        ),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
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
        index=True,
    )

    device_type: Mapped[MonitoringDeviceType] = mapped_column(
        Enum(
            MonitoringDeviceType,
            name="monitoring_device_type",
        ),
        nullable=False,
    )

    device_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    device_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    battery_percent: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )

    is_worn: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    not_worn_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    connection_status: Mapped[DeviceConnectionStatus] = mapped_column(
        Enum(
            DeviceConnectionStatus,
            name="device_connection_status",
        ),
        nullable=False,
        default=DeviceConnectionStatus.UNKNOWN,
        server_default="UNKNOWN",
    )

    reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )

    status_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
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