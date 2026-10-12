from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class VitalFacts(BaseModel):
    unit: str
    latest: float | None = None
    average: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    reading_count: int = Field(ge=0)
    normal_min: float | None = None
    normal_max: float | None = None


class ActivityFacts(BaseModel):
    average_steps_per_day: float | None = None
    highest_steps: int | None = None
    highest_date: date | None = None
    lowest_steps: int | None = None
    lowest_date: date | None = None
    days_with_data: int = Field(ge=0)


class SleepFacts(BaseModel):
    latest_duration_seconds: int | None = None
    latest_date: date | None = None
    average_duration_seconds: float | None = None
    longest_duration_seconds: int | None = None
    shortest_duration_seconds: int | None = None
    nights_with_data: int = Field(ge=0)


class AlertFacts(BaseModel):
    total: int = Field(ge=0)
    active: int = Field(ge=0)
    warning: int = Field(ge=0)
    critical: int = Field(ge=0)


class InsightFactPacket(BaseModel):
    patient_id: UUID
    range: Literal["7d"] = "7d"
    from_at: datetime
    to_at: datetime
    heart_rate: VitalFacts
    spo2: VitalFacts
    activity: ActivityFacts
    sleep: SleepFacts
    alerts: AlertFacts
    missing_data: list[Literal["heart_rate", "spo2", "activity", "sleep"]]
