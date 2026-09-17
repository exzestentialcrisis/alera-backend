from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.nudges.model import PatientNudgeType


class PatientNudgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nudge_type: PatientNudgeType
    client_action_id: UUID


class PatientNudgeRead(BaseModel):
    nudge_id: UUID
    patient_id: UUID
    sent_by_user_id: UUID
    nudge_type: PatientNudgeType
    client_action_id: UUID
    created_at: datetime
    idempotent: bool
