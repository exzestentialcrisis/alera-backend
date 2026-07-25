from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.alert_actions.model import AlertAction, AlertActionType
from app.alerts.model import Alert, AlertStatus
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    EventEvaluation,
    MonitoringState,
)
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.users.model import User, UserRole

pytestmark = pytest.mark.integration


NOW = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)


def make_alert(patient, **overrides):
    values = {
        "patient_id": patient.patient_id,
        "condition_key": ConditionKey.HR_HIGH,
        "severity": EvaluationSeverity.WARNING,
        "detected_at": NOW,
        "confirmed_at": NOW + timedelta(minutes=2),
    }
    values.update(overrides)
    return Alert(**values)


def make_event(patient):
    return HealthEvent(
        patient_id=patient.patient_id,
        external_event_id=f"alert-test-{uuid4()}",
        metric_type=MetricType.HEART_RATE,
        numeric_value=110,
        metric_unit="bpm",
        recorded_at=NOW,
        validation_status=ValidationStatus.VALID_REALTIME,
    )


def make_evaluation(event, alert_id=None):
    return EventEvaluation(
        event_id=event.event_id,
        alert_id=alert_id,
        condition_key=ConditionKey.HR_HIGH,
        threshold_value_used=100,
        threshold_met=True,
        persistence_met=True,
        previous_state=MonitoringState.ELEVATED,
        new_state=MonitoringState.WARNING,
        severity=EvaluationSeverity.WARNING,
        evaluation_reason="Integration test evaluation",
    )


def test_create_active_warning_alert(db_session, patient):
    alert = make_alert(patient)
    db_session.add(alert)
    db_session.commit()

    assert alert.status is AlertStatus.ACTIVE
    assert alert.severity is EvaluationSeverity.WARNING


def test_create_alert_action_with_caregiver(db_session, patient):
    caregiver = User(full_name="Test Caregiver", role=UserRole.CAREGIVER)
    alert = make_alert(patient)
    db_session.add_all([caregiver, alert])
    db_session.flush()
    action = AlertAction(
        alert_id=alert.alert_id,
        performed_by_user_id=caregiver.user_id,
        action_type=AlertActionType.ACKNOWLEDGE,
        previous_status=AlertStatus.ACTIVE,
        new_status=AlertStatus.ACKNOWLEDGED,
    )
    db_session.add(action)
    db_session.commit()

    assert action.performed_by_user_id == caregiver.user_id
    assert action.action_metadata == {}


def test_create_system_action_without_user(db_session, patient):
    alert = make_alert(patient)
    db_session.add(alert)
    db_session.flush()
    action = AlertAction(
        alert_id=alert.alert_id,
        performed_by_user_id=None,
        action_type=AlertActionType.ESCALATE,
    )
    db_session.add(action)
    db_session.commit()

    assert action.performed_by_user_id is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"detected_at": NOW, "confirmed_at": NOW - timedelta(seconds=1)},
        {
            "detected_at": NOW,
            "confirmed_at": NOW + timedelta(minutes=2),
            "resolved_at": NOW + timedelta(minutes=1),
            "status": AlertStatus.RESOLVED,
        },
    ],
)
def test_reject_invalid_alert_timestamp_order(
    db_session,
    patient,
    overrides,
):
    db_session.add(make_alert(patient, **overrides))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_reject_second_current_alert_for_patient_condition(db_session, patient):
    db_session.add(make_alert(patient, status=AlertStatus.ACTIVE))
    db_session.commit()
    db_session.add(make_alert(patient, status=AlertStatus.ACKNOWLEDGED))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_allow_multiple_historical_resolved_alerts(db_session, patient):
    for offset in (0, 10):
        detected_at = NOW + timedelta(minutes=offset)
        db_session.add(
            make_alert(
                patient,
                status=AlertStatus.RESOLVED,
                detected_at=detected_at,
                confirmed_at=detected_at + timedelta(minutes=2),
                resolved_at=detected_at + timedelta(minutes=3),
            )
        )
    db_session.commit()


def test_event_evaluation_allows_null_alert_id(db_session, patient):
    event = make_event(patient)
    db_session.add(event)
    db_session.flush()
    evaluation = make_evaluation(event)
    db_session.add(evaluation)
    db_session.commit()

    assert evaluation.alert_id is None


def test_event_evaluation_references_valid_alert(db_session, patient):
    event = make_event(patient)
    alert = make_alert(patient)
    db_session.add_all([event, alert])
    db_session.flush()
    evaluation = make_evaluation(event, alert.alert_id)
    db_session.add(evaluation)
    db_session.commit()

    assert evaluation.alert_id == alert.alert_id


def test_event_evaluation_rejects_missing_alert(db_session, patient):
    event = make_event(patient)
    db_session.add(event)
    db_session.flush()
    db_session.add(make_evaluation(event, uuid4()))

    with pytest.raises(IntegrityError):
        db_session.commit()
