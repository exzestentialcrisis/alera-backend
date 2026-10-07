import enum
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.event_evaluations.model import EvaluationSeverity
from app.household_access.schema import CaregiverAssignmentResponse
from app.monitoring_devices.schema import MonitoringDeviceRead
from app.patients.model import IntegrationStatus, Sex
from app.users.model import AccountStatus

RELATIONSHIP_LABEL_MAX_LENGTH = 50


def normalize_relationship_label(value: str | None) -> str | None:
    """Normalize an optional caregiver-specific relationship label."""
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


class PatientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str = Field(min_length=1, max_length=150)
    birthdate: date | None = None
    sex: Sex | None = None
    phone_number: str | None = Field(default=None, max_length=11)
    address_or_room: str | None = None
    emergency_contact_name: str | None = Field(default=None, max_length=150)
    emergency_contact_phone: str | None = Field(default=None, max_length=30)
    known_conditions: str | None = None
    medications: str | None = None
    baseline_heart_rate: Decimal | None = Field(
        default=None, gt=0, max_digits=6, decimal_places=2
    )
    baseline_spo2: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
    monitoring_notes: str | None = None
    relationship_label: str | None = Field(
        default=None,
        max_length=RELATIONSHIP_LABEL_MAX_LENGTH,
    )

    @field_validator("relationship_label", mode="before")
    @classmethod
    def normalize_relationship(cls, value: str | None) -> str | None:
        return normalize_relationship_label(value)


class PatientUpdate(BaseModel):
    """Full replacement of editable profile fields from Edit Patient."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str = Field(min_length=1, max_length=150)
    birthdate: date | None
    sex: Sex | None
    phone_number: str | None = Field(max_length=11)
    address_or_room: str | None
    emergency_contact_name: str | None = Field(max_length=150)
    emergency_contact_phone: str | None = Field(max_length=30)
    known_conditions: str | None
    medications: str | None
    monitoring_notes: str | None
    relationship_label: str | None = Field(
        max_length=RELATIONSHIP_LABEL_MAX_LENGTH,
    )

    @field_validator("relationship_label", mode="before")
    @classmethod
    def normalize_relationship(cls, value: str | None) -> str | None:
        return normalize_relationship_label(value)


class PatientCreated(PatientCreate):
    patient_id: UUID
    user_id: UUID
    household_id: UUID
    account_status: AccountStatus
    archived_at: datetime | None
    assignment: CaregiverAssignmentResponse | None
    created_at: datetime
    profile_photo_url: str | None = None


class MonitoringStatus(str, enum.Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    STABLE = "STABLE"
    NO_DATA = "NO_DATA"


class LatestReading(BaseModel):
    value: Decimal
    unit: str | None
    recorded_at: datetime


class CurrentHealthSummary(BaseModel):
    latest_heart_rate: LatestReading | None
    latest_spo2: LatestReading | None

    today_steps: int | None = None
    steps_updated_at: datetime | None = None

    latest_sleep_duration_seconds: int | None = None
    latest_sleep_date: date | None = None

    last_check_in: datetime | None
    active_alert_count: int
    highest_active_alert_severity: EvaluationSeverity | None
    monitoring_status: MonitoringStatus
    device_connection_status: IntegrationStatus
    last_device_sync_at: datetime | None


class PatientListItem(BaseModel):
    patient_id: UUID
    user_id: UUID
    household_id: UUID
    full_name: str
    birthdate: date | None
    sex: Sex | None
    phone_number: str | None
    address_or_room: str | None
    account_status: AccountStatus
    created_at: datetime
    current_summary: CurrentHealthSummary
    profile_photo_url: str | None = None
    relationship_label: str | None = None


class PatientListResponse(BaseModel):
    items: list[PatientListItem]
    total: int
    limit: int
    offset: int


class ThresholdMode(str, enum.Enum):
    DEFAULT = "DEFAULT"
    CUSTOM = "CUSTOM"


class PatientAccessStatus(str, enum.Enum):
    NOT_CONNECTED = "NOT_CONNECTED"
    INVITE_PENDING = "INVITE_PENDING"
    CONNECTED = "CONNECTED"


class PatientAccessSummary(BaseModel):
    """Enrollment state derived from patient access-code records.

    CONNECTED means the patient successfully redeemed an access code; it does
    not describe smartwatch connectivity or sync state.
    """

    status: PatientAccessStatus
    pending_access_code_id: UUID | None
    pending_expires_at: datetime | None
    connected_at: datetime | None


class PatientDetail(PatientCreated):
    current_summary: CurrentHealthSummary
    patient_access: PatientAccessSummary
    monitoring_devices: list[MonitoringDeviceRead]

    normal_hr_min: int
    normal_hr_max: int
    usual_spo2_min: int
    usual_spo2_max: int | None
    threshold_mode: ThresholdMode


class MonitoringSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normal_hr_min: int | None = Field(default=None, gt=0)
    normal_hr_max: int | None = Field(default=None, gt=0)
    usual_spo2_min: int | None = Field(default=None, ge=0, le=100)
    usual_spo2_max: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def reject_empty_or_null_non_nullable_fields(self):
        if not self.model_fields_set:
            raise ValueError("At least one monitoring setting must be provided.")
        for field in ("normal_hr_min", "normal_hr_max", "usual_spo2_min"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} may not be null.")
        return self


class MonitoringSettingsResponse(BaseModel):
    patient_id: UUID
    threshold_mode: ThresholdMode
    normal_hr_min: int
    normal_hr_max: int
    usual_spo2_min: int
    usual_spo2_max: int | None
    updated_at: datetime


class PatientProfilePhotoResponse(BaseModel):
    patient_id: UUID
    profile_photo_url: str
