from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.users.model import UserRole


class CaregiverLoginRequest(BaseModel):
    household_code: str = Field(min_length=1, max_length=32)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class ActorProfile(BaseModel):
    user_id: UUID
    full_name: str
    role: UserRole
    household_id: UUID
    household_name: str
    household_code: str


class CaregiverLoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    actor: ActorProfile


class PatientAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Authentication normalizes malformed values to the generic 401 response.
    access_code: str = Field(
        description="One-time patient access code, for example XXXX-XXXX-XXXX."
    )
