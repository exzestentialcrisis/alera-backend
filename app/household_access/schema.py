from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CaregiverAssignmentCreate(BaseModel):
    caregiver_user_id: UUID
    patient_id: UUID


class CaregiverAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    caregiver_user_id: UUID
    patient_id: UUID
    assigned_by_user_id: UUID
    assigned_at: datetime
    unassigned_at: datetime | None


class PatientAccessCodeCreate(BaseModel):
    expires_in_hours: int = Field(default=24, ge=1, le=168)


class PatientAccessCodeIssueResponse(BaseModel):
    access_code_id: UUID
    patient_id: UUID
    access_code: str
    created_by_user_id: UUID
    created_at: datetime
    expires_at: datetime
    status: str


class PatientAccessCodeResponse(BaseModel):
    access_code_id: UUID
    patient_id: UUID
    created_by_user_id: UUID
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None
    status: str
