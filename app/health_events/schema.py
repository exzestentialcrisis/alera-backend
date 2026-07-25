from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.health_events.model import MetricType, ValidationStatus


class HealthEventCreate(BaseModel):
    NUMERIC_QUANTUM: ClassVar[Decimal] = Decimal("0.01")
    NUMERIC_MAX: ClassVar[Decimal] = Decimal("99999999.99")

    patient_id: UUID
    external_event_id: str | None = Field(default=None, max_length=150)

    metric_type: MetricType

    numeric_value: Decimal | None = None
    text_value: str | None = Field(default=None, max_length=100)
    boolean_value: bool | None = None

    metric_unit: str | None = Field(default=None, max_length=30)

    recorded_at: datetime
    validation_status: ValidationStatus
    validation_reason: str | None = None

    raw_payload: dict = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("recorded_at")
    @classmethod
    def normalize_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone offset.")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_metric_value(self) -> "HealthEventCreate":
        if self.validation_status == ValidationStatus.INVALID:
            if not self.validation_reason or not self.validation_reason.strip():
                raise ValueError("validation_reason is required for INVALID events.")
        elif self.validation_status == ValidationStatus.VALID_REALTIME:
            if self.validation_reason is not None:
                raise ValueError(
                    "validation_reason must not be provided for VALID_REALTIME events."
                )

        if self.numeric_value is not None:
            if not self.numeric_value.is_finite():
                raise ValueError("numeric_value must be finite.")
            try:
                self.numeric_value = self.numeric_value.quantize(
                    self.NUMERIC_QUANTUM,
                    rounding=ROUND_HALF_UP,
                )
            except InvalidOperation as exc:
                raise ValueError("numeric_value is outside the supported range.") from exc
            if abs(self.numeric_value) > self.NUMERIC_MAX:
                raise ValueError("numeric_value must fit NUMERIC(10,2).")

        # Invalid readings are retained for audit and are deliberately not
        # required to satisfy the physical-value contract.
        if self.validation_status == ValidationStatus.INVALID:
            return self

        if self.metric_type == MetricType.HEART_RATE:
            if self.numeric_value is None:
                raise ValueError("numeric_value is required for HEART_RATE.")
            if not self.numeric_value.is_finite() or self.numeric_value <= 0:
                raise ValueError("Heart rate must be greater than 0 bpm.")

        if self.metric_type == MetricType.SPO2:
            if self.numeric_value is None:
                raise ValueError("numeric_value is required for SPO2.")
            if not self.numeric_value.is_finite() or not 0 <= self.numeric_value <= 100:
                raise ValueError("SpO₂ must be between 0 and 100 percent.")

        return self


class HealthEventResponse(HealthEventCreate):
    event_id: UUID
    received_at: datetime
    created_at: datetime

    model_config = {
        "from_attributes": True,
    }
