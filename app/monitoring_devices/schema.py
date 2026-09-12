from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.monitoring_devices.model import (
    DeviceConnectionStatus,
    DeviceNetworkType,
    MonitoringDeviceType,
)


class DeviceStatusUpsert(BaseModel):
    patient_id: UUID

    device_type: MonitoringDeviceType

    device_name: str | None = Field(
        default=None,
        max_length=100,
    )

    device_model: str | None = Field(
        default=None,
        max_length=100,
    )

    battery_percent: int | None = Field(
        default=None,
        ge=0,
        le=100,
    )

    connection_status: DeviceConnectionStatus

    network_type: DeviceNetworkType = DeviceNetworkType.UNKNOWN

    reported_at: datetime

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("reported_at")
    @classmethod
    def validate_reported_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "reported_at must include a timezone offset."
            )

        value = value.astimezone(timezone.utc)

        if value > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError(
                "reported_at cannot be significantly in the future."
            )

        return value


class DeviceStatusResponse(BaseModel):
    device_id: UUID
    patient_id: UUID

    device_type: MonitoringDeviceType

    device_name: str | None
    device_model: str | None

    battery_percent: int | None

    connection_status: DeviceConnectionStatus
    network_type: DeviceNetworkType

    reported_at: datetime | None
    last_seen_at: datetime
    status_changed_at: datetime

    created_at: datetime
    updated_at: datetime

    applied: bool

    model_config = ConfigDict(
        from_attributes=True,
    )