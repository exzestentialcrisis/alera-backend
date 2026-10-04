from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.help_requests.model import HelpRequestStatus


class HelpRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_action_id: UUID
    message: str | None = Field(default=None, max_length=500)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class HelpRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    help_request_id: UUID
    patient_id: UUID
    status: HelpRequestStatus
    message: str | None
    client_action_id: UUID
    requested_at: datetime
    acknowledged_by_user_id: UUID | None
    acknowledged_at: datetime | None
    resolved_by_user_id: UUID | None
    resolved_at: datetime | None
    updated_at: datetime
    idempotent: bool = False
