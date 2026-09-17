from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.activity.model import ActivityType, SleepType


class ActivityDailyDataInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_steps: int | None = Field(default=None, ge=0)
    total_duration_seconds: int | None = Field(default=None, ge=0)

    total_distance_meters: Decimal | None = Field(
        default=None,
        ge=0,
    )

    first_movement_at: datetime | None = None
    last_movement_at: datetime | None = None

    @model_validator(mode="after")
    def validate_movement_times(self):
        if (
            self.first_movement_at is not None
            and self.last_movement_at is not None
            and self.last_movement_at < self.first_movement_at
        ):
            raise ValueError(
                "last_movement_at must not be before first_movement_at."
            )

        return self


class ActivitySessionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_session_id: str | None = Field(
        default=None,
        max_length=200,
    )

    sleep_type: SleepType | None = None

    started_at: datetime
    ended_at: datetime | None = None

    steps: int | None = Field(default=None, ge=0)

    distance_meters: Decimal | None = Field(
        default=None,
        ge=0,
    )

    source: str | None = Field(
        default=None,
        max_length=50,
    )

    @model_validator(mode="after")
    def validate_session(self):
        if (
            self.ended_at is not None
            and self.ended_at < self.started_at
        ):
            raise ValueError(
                "ended_at must not be before started_at."
            )

        return self


class ActivityDataUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient_id: UUID
    activity_date: date
    activity_type: ActivityType

    daily: ActivityDailyDataInput | None = None
    sessions: list[ActivitySessionInput] = Field(
        default_factory=list,
    )

    @model_validator(mode="after")
    def validate_activity_payload(self):
        if self.daily is None and not self.sessions:
            raise ValueError(
                "At least one of daily or sessions must be provided."
            )

        if self.activity_type is not ActivityType.SLEEP:
            for session in self.sessions:
                if session.sleep_type is not None:
                    raise ValueError(
                        "sleep_type may only be provided for SLEEP activity."
                    )

        return self


class ActivitySessionRead(BaseModel):
    activity_session_id: UUID
    external_session_id: str | None

    sleep_type: SleepType | None

    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None

    steps: int | None
    distance_meters: Decimal | None

    source: str | None

    created_at: datetime
    updated_at: datetime


class ActivityDailyDataRead(BaseModel):
    activity_daily_data_id: UUID

    total_steps: int | None
    total_duration_seconds: int
    total_distance_meters: Decimal | None

    session_count: int

    first_movement_at: datetime | None
    last_movement_at: datetime | None

    created_at: datetime
    updated_at: datetime


class ActivityDataResponse(BaseModel):
    activity_data_id: UUID

    patient_id: UUID
    activity_date: date
    activity_type: ActivityType

    daily: ActivityDailyDataRead | None
    sessions: list[ActivitySessionRead]

    created_at: datetime
    updated_at: datetime