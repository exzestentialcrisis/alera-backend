from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

import app.event_evaluations.service as evaluation_service
from app.event_evaluations.model import ConditionKey, MonitoringState
from app.health_events.model import HealthEvent, MetricType, ValidationStatus


def evaluate(monkeypatch, value, minimum=95):
    db = Mock()
    db.get.return_value = SimpleNamespace(usual_spo2_min=minimum)
    monkeypatch.setattr(evaluation_service, "update_condition_tracker", Mock())
    monkeypatch.setattr(
        evaluation_service,
        "process_immediate_critical_alert",
        Mock(),
    )
    monkeypatch.setattr(
        evaluation_service,
        "process_consecutive_spo2_warning",
        Mock(),
    )
    event = HealthEvent(
        event_id=uuid4(),
        patient_id=uuid4(),
        metric_type=MetricType.SPO2,
        numeric_value=Decimal(value),
        validation_status=ValidationStatus.VALID_REALTIME,
    )
    return evaluation_service.evaluate_spo2_event(db, event)


@pytest.mark.parametrize(
    ("value", "state", "condition"),
    [
        ("97", MonitoringState.STABLE, ConditionKey.SPO2_NORMAL),
        ("94", MonitoringState.STABLE, ConditionKey.SPO2_NORMAL),
        ("93", MonitoringState.ELEVATED, ConditionKey.SPO2_LOW),
        ("95", MonitoringState.STABLE, ConditionKey.SPO2_NORMAL),
        ("90", MonitoringState.ELEVATED, ConditionKey.SPO2_LOW),
        ("89.99", MonitoringState.CRITICAL, ConditionKey.SPO2_LOW),
    ],
)
def test_spo2_boundaries(monkeypatch, value, state, condition):
    result = evaluate(monkeypatch, value)
    assert result.new_state == state
    assert result.condition_key == condition


def test_warning_band_is_independent_of_patient_baseline(monkeypatch):
    assert evaluate(monkeypatch, "93", minimum=92).new_state == MonitoringState.ELEVATED
    assert evaluate(monkeypatch, "91", minimum=92).new_state == MonitoringState.ELEVATED
