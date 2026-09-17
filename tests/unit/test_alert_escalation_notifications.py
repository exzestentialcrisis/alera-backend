from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

import app.alerts.service as alert_service
from app.alert_actions.model import AlertAction, AlertActionType
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
        confirmed_at=None,
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
    monkeypatch.setattr(alert_service, "_critical_confirmation_met", lambda *_: True)
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
    escalation = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], AlertAction)
    )
    assert escalation.action_type is AlertActionType.ESCALATE
    assert escalation.performed_by_user_id is None
    assert escalation.previous_status is status
    assert escalation.new_status is status
    assert escalation.action_metadata["previous_severity"] == "WARNING"
    assert escalation.action_metadata["new_severity"] == "CRITICAL"
    assert escalation.action_metadata["seconds_since_detected"] == 0
    assert escalation.action_metadata["seconds_since_confirmed"] == 0
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
    monkeypatch.setattr(alert_service, "_critical_confirmation_met", lambda *_: True)
    monkeypatch.setattr(alert_service, "queue_alert_notification", queued)

    result = alert_service.process_immediate_critical_alert(
        db,
        event,
        evaluation,
        tracker_result,
    )

    assert result is alert
    assert evaluation.alert_id == alert.alert_id
    db.add.assert_not_called()
    queued.assert_not_called()


def test_critical_confirmation_accepts_prior_critical_after_ten_seconds():
    event, evaluation, _, _ = critical_context(
        AlertStatus.ACTIVE,
        EvaluationSeverity.CRITICAL,
    )
    db = Mock()
    db.execute.return_value.all.return_value = [
        (
            event.recorded_at - timedelta(seconds=15),
            evaluation.condition_key,
            EvaluationSeverity.CRITICAL,
        )
    ]

    assert alert_service._critical_confirmation_met(db, event, evaluation) is True


def test_normal_sample_between_critical_samples_cancels_confirmation():
    event, evaluation, _, _ = critical_context(
        AlertStatus.ACTIVE,
        EvaluationSeverity.CRITICAL,
    )
    db = Mock()
    db.execute.return_value.all.return_value = [
        (
            event.recorded_at - timedelta(seconds=5),
            ConditionKey.HR_NORMAL,
            EvaluationSeverity.INFO,
        ),
        (
            event.recorded_at - timedelta(seconds=15),
            evaluation.condition_key,
            EvaluationSeverity.CRITICAL,
        ),
    ]

    assert alert_service._critical_confirmation_met(db, event, evaluation) is False
