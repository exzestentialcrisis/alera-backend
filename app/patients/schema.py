from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.household_access.schema import CaregiverAssignmentResponse
from app.patients.model import Sex
from app.users.model import AccountStatus


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
    baseline_heart_rate: Decimal | None = Field(default=None, gt=0, max_digits=6, decimal_places=2)
    baseline_spo2: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    monitoring_notes: str | None = None


class PatientCreated(PatientCreate):
    patient_id: UUID
    user_id: UUID
    household_id: UUID
    account_status: AccountStatus
    archived_at: datetime | None
    assignment: CaregiverAssignmentResponse | None
    created_at: datetime
