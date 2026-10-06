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
    patient_display_name: str | None = None
    idempotent: bool = False


class HelpRequestListResponse(BaseModel):
    items: list[HelpRequestRead]
    total: int
    limit: int
    offset: int


class HelpRequestNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_action_id: UUID
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("note must not be blank")
        return value


class HelpRequestNoteRead(BaseModel):
    help_request_note_id: UUID
    help_request_id: UUID
    author_user_id: UUID
    client_action_id: UUID
    note: str
    created_at: datetime
    author_display_name: str | None = None
    idempotent: bool = False


class HelpRequestNoteListResponse(BaseModel):
    items: list[HelpRequestNoteRead]
    total: int
    limit: int
    offset: int
