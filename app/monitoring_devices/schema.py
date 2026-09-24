from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.monitoring_devices.model import (
    DeviceConnectionStatus,
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

    is_worn: bool | None = None

    connection_status: DeviceConnectionStatus

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
    
    @model_validator(mode="after")
    def reject_explicit_logged_out_status(self):
        if self.connection_status is DeviceConnectionStatus.LOGGED_OUT:
            raise ValueError(
                "LOGGED_OUT may only be set through the patient logout endpoint."
            )
    
        return self

class MonitoringDeviceRead(BaseModel):
    device_id: UUID
    patient_id: UUID

    device_type: MonitoringDeviceType

    device_name: str | None
    device_model: str | None

    battery_percent: int | None
    is_worn: bool | None
    not_worn_since: datetime | None

    connection_status: DeviceConnectionStatus

    reported_at: datetime | None
    last_seen_at: datetime
    status_changed_at: datetime

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
    )

class DeviceStatusResponse(BaseModel):
    device_id: UUID
    patient_id: UUID

    device_type: MonitoringDeviceType

    device_name: str | None
    device_model: str | None

    battery_percent: int | None
    is_worn: bool | None
    not_worn_since: datetime | None

    connection_status: DeviceConnectionStatus

    reported_at: datetime | None
    last_seen_at: datetime
    status_changed_at: datetime

    created_at: datetime
    updated_at: datetime

    applied: bool

    model_config = ConfigDict(
        from_attributes=True,
    )

class PatientLogoutResponse(BaseModel):
    patient_id: UUID
    device_id: UUID
    connection_status: DeviceConnectionStatus
    status_changed_at: datetime    