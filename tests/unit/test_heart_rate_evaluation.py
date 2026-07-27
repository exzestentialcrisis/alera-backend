from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

import app.event_evaluations.service as evaluation_service
from app.event_evaluations.model import ConditionKey, MonitoringState
from app.health_events.model import HealthEvent, MetricType, ValidationStatus


def evaluate(monkeypatch, value, hr_min=60, hr_max=100):
    db = Mock()
    db.get.return_value = SimpleNamespace(
        normal_hr_min=hr_min,
        normal_hr_max=hr_max,
    )
    monkeypatch.setattr(evaluation_service, "update_condition_tracker", Mock())
    monkeypatch.setattr(
        evaluation_service,
        "process_immediate_critical_alert",
        Mock(),
    )
    monkeypatch.setattr(
        evaluation_service,
        "process_persistent_hr_warning",
        Mock(),
    )
    event = HealthEvent(
        event_id=uuid4(),
        patient_id=uuid4(),
        metric_type=MetricType.HEART_RATE,
        numeric_value=Decimal(value),
        validation_status=ValidationStatus.VALID_REALTIME,
    )
    return evaluation_service.evaluate_heart_rate_event(db, event)


@pytest.mark.parametrize(
    ("value", "state", "condition"),
    [
        ("78", MonitoringState.STABLE, ConditionKey.HR_NORMAL),
        ("110", MonitoringState.ELEVATED, ConditionKey.HR_HIGH),
        ("45", MonitoringState.ELEVATED, ConditionKey.HR_LOW),
        ("165", MonitoringState.CRITICAL, ConditionKey.HR_HIGH),
        ("60", MonitoringState.STABLE, ConditionKey.HR_NORMAL),
        ("59", MonitoringState.ELEVATED, ConditionKey.HR_LOW),
        ("100", MonitoringState.STABLE, ConditionKey.HR_NORMAL),
        ("101", MonitoringState.ELEVATED, ConditionKey.HR_HIGH),
        ("150", MonitoringState.ELEVATED, ConditionKey.HR_HIGH),
        ("151", MonitoringState.CRITICAL, ConditionKey.HR_HIGH),
    ],
)
def test_heart_rate_boundaries(monkeypatch, value, state, condition):
    result = evaluate(monkeypatch, value)
    assert result.new_state == state
    assert result.condition_key == condition


def test_custom_thresholds_are_used(monkeypatch):
    assert evaluate(monkeypatch, "84", hr_min=50, hr_max=85).new_state == MonitoringState.STABLE
    assert evaluate(monkeypatch, "86", hr_min=50, hr_max=85).condition_key == ConditionKey.HR_HIGH


def test_global_critical_rule_precedes_patient_maximum(monkeypatch):
    result = evaluate(monkeypatch, "151", hr_min=60, hr_max=180)
    assert result.new_state == MonitoringState.CRITICAL
    assert result.condition_key == ConditionKey.HR_HIGH
