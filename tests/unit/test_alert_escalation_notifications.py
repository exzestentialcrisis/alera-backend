from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

import app.alerts.service as alert_service
from app.alerts.model import Alert, AlertStatus
from app.condition_trackers.service import ConditionTrackerUpdateResult
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    EventEvaluation,
)
from app.health_events.model import HealthEvent, MetricType, ValidationStatus


BASE_TIME = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


def critical_context(status, severity):
    patient_id = uuid4()
    event = HealthEvent(
        event_id=uuid4(),
        patient_id=patient_id,
        metric_type=MetricType.HEART_RATE,
        numeric_value=151,
        recorded_at=BASE_TIME,
        validation_status=ValidationStatus.VALID_REALTIME,
    )
    evaluation = EventEvaluation(
        evaluation_id=uuid4(),
        event_id=event.event_id,
        condition_key=ConditionKey.HR_HIGH,
        severity=EvaluationSeverity.CRITICAL,
    )
    alert = Alert(
        alert_id=uuid4(),
        patient_id=patient_id,
        condition_key=ConditionKey.HR_HIGH,
        severity=severity,
        status=status,
        detected_at=BASE_TIME,
        confirmed_at=BASE_TIME,
    )
    tracker = SimpleNamespace(
        active=True,
        last_event_id=event.event_id,
        started_at=BASE_TIME,
    )
    result = ConditionTrackerUpdateResult(applied=True, tracker=tracker)
    return event, evaluation, alert, result


@pytest.mark.parametrize(
    "status",
    [AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED],
)
def test_warning_to_critical_queues_one_escalation(monkeypatch, status):
    event, evaluation, alert, tracker_result = critical_context(
        status,
        EvaluationSeverity.WARNING,
    )
    db = Mock()
    queued = Mock()
    monkeypatch.setattr(
        alert_service,
        "_find_unresolved_occurrence_alert",
        lambda *_: alert,
    )
    monkeypatch.setattr(alert_service, "queue_alert_notification", queued)

    result = alert_service.process_immediate_critical_alert(
        db,
        event,
        evaluation,
        tracker_result,
    )

    assert result is alert
    assert alert.severity is EvaluationSeverity.CRITICAL
    assert alert.status is status
    assert evaluation.alert_id == alert.alert_id
    queued.assert_called_once_with(
        db,
        alert,
        include_acknowledged=True,
    )


def test_later_critical_reading_does_not_repeat_notification(monkeypatch):
    event, evaluation, alert, tracker_result = critical_context(
        AlertStatus.ACTIVE,
        EvaluationSeverity.CRITICAL,
    )
    db = Mock()
    queued = Mock()
    monkeypatch.setattr(
        alert_service,
        "_find_unresolved_occurrence_alert",
        lambda *_: alert,
    )
    monkeypatch.setattr(alert_service, "queue_alert_notification", queued)

    result = alert_service.process_immediate_critical_alert(
        db,
        event,
        evaluation,
        tracker_result,
    )

    assert result is alert
    assert evaluation.alert_id == alert.alert_id
    queued.assert_not_called()
