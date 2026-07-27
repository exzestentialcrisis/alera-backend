import enum
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.alert_actions.model import AlertActionType
from app.alerts.model import AlertStatus
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    MonitoringState,
)
from app.health_events.model import MetricType, ValidationStatus


class InterventionType(str, enum.Enum):
    PATIENT_CHECK = "PATIENT_CHECK"
    REST_AND_MONITOR = "REST_AND_MONITOR"
    SENSOR_REPOSITIONED = "SENSOR_REPOSITIONED"
    CONTACTED_FAMILY = "CONTACTED_FAMILY"
    CONTACTED_CAREGIVER = "CONTACTED_CAREGIVER"
    CONTACTED_CLINIC = "CONTACTED_CLINIC"
    CONTACTED_EMERGENCY_SERVICES = "CONTACTED_EMERGENCY_SERVICES"
    OTHER = "OTHER"


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_id: UUID
    patient_id: UUID
    condition_key: ConditionKey
    severity: EvaluationSeverity
    status: AlertStatus
    detected_at: datetime
    confirmed_at: datetime
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AlertActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_action_id: UUID
    alert_id: UUID
    performed_by_user_id: UUID | None
    action_type: AlertActionType
    action_note: str | None
    previous_status: AlertStatus | None
    new_status: AlertStatus | None
    action_metadata: dict
    performed_at: datetime


class HealthEventSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    patient_id: UUID
    external_event_id: str | None
    metric_type: MetricType
    numeric_value: Decimal | None
    recorded_at: datetime
    validation_status: ValidationStatus


class EventEvaluationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    evaluation_id: UUID
    event_id: UUID
    alert_id: UUID | None
    condition_key: ConditionKey
    threshold_value_used: Decimal | None
    threshold_met: bool
    persistence_met: bool
    previous_state: MonitoringState
    new_state: MonitoringState
    severity: EvaluationSeverity
    evaluation_reason: str
    evaluated_at: datetime


class AlertDetail(AlertRead):
    triggering_event: HealthEventSummary | None
    triggering_evaluation: EventEvaluationSummary | None
    latest_action: AlertActionRead | None


class AlertListResponse(BaseModel):
    items: list[AlertRead]
    total: int
    limit: int
    offset: int


class AlertActionHistoryResponse(BaseModel):
    items: list[AlertActionRead]


class AlertActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OptionalNoteRequest(AlertActionRequest):
    note: str | None = None

    @field_validator("note")
    @classmethod
    def normalize_optional_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("note must not be blank")
        return value


class FalseAlarmRequest(AlertActionRequest):
    reason: str

    @field_validator("reason")
    @classmethod
    def require_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value


class NoteRequest(AlertActionRequest):
    note: str

    @field_validator("note")
    @classmethod
    def require_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("note must not be blank")
        return value


class InterventionRequest(AlertActionRequest):
    intervention_type: InterventionType
    note: str

    @field_validator("note")
    @classmethod
    def require_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("note must not be blank")
        return value


class AlertActionResponse(BaseModel):
    alert: AlertRead
    action: AlertActionRead | None
    idempotent: bool
