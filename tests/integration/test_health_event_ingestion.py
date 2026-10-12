from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.condition_trackers.model import ConditionTracker
from app.event_evaluations.model import EventEvaluation, MonitoringState
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event
from app.alerts.model import Alert

from app.auth.security import create_access_token
from tests.integration.test_caregiver_auth import (
    JWT_SECRET,
    api_app,
    make_household,
    request,
)


pytestmark = pytest.mark.integration


def create(db_session, patient, **changes):
    payload = {
        "patient_id": patient.patient_id,
        "metric_type": MetricType.HEART_RATE,
        "numeric_value": Decimal("78"),
        "recorded_at": datetime(2026, 7, 17, 5, tzinfo=timezone.utc),
        "validation_status": ValidationStatus.VALID_REALTIME,
    }
    payload.update(changes)
    return create_health_event(db_session, HealthEventCreate.model_validate(payload))


def test_ingestion_persists_event_evaluation_and_tracker(db_session, patient):
    event = create(db_session, patient, numeric_value="110")
    evaluation = db_session.scalar(
        select(EventEvaluation).where(EventEvaluation.event_id == event.event_id)
    )
    tracker = db_session.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient.patient_id,
            ConditionTracker.active.is_(True),
        )
    )
    assert evaluation.new_state == MonitoringState.ELEVATED
    assert tracker.last_event_id == event.event_id


def test_normalized_value_is_used_for_storage_and_evaluation(db_session, patient):
    event = create(db_session, patient, numeric_value="150.995")
    evaluation = db_session.scalar(
        select(EventEvaluation).where(EventEvaluation.event_id == event.event_id)
    )
    assert event.numeric_value == Decimal("151.00")
    assert evaluation.new_state == MonitoringState.CRITICAL
    assert "151.00" in evaluation.evaluation_reason


@pytest.mark.parametrize("metric_type", [MetricType.HEART_RATE, MetricType.SPO2])
def test_invalid_metric_is_stored_without_evaluation_or_tracker(
    db_session,
    patient,
    metric_type,
):
    event = create(
        db_session,
        patient,
        metric_type=metric_type,
        numeric_value=None,
        validation_status=ValidationStatus.INVALID,
        validation_reason="sensor quality failure",
    )
    assert db_session.get(HealthEvent, event.event_id) is not None
    assert db_session.scalar(select(func.count(EventEvaluation.evaluation_id))) == 0
    assert db_session.scalar(select(func.count(ConditionTracker.condition_tracker_id))) == 0


def test_delayed_usable_new_event_does_not_update_realtime_tracker(
    db_session,
    patient,
):
    event = create(
        db_session,
        patient,
        numeric_value="110",
        validation_status=ValidationStatus.DELAYED_USABLE,
        validation_reason="uploaded after reconnect",
    )

    assert db_session.get(HealthEvent, event.event_id) is not None
    assert db_session.scalar(select(EventEvaluation)) is not None
    assert db_session.scalar(select(ConditionTracker)) is None

    
# Phase 3.2 — Patient-session authorization tests

def health_payload(patient_id):
    return {
        "patient_id": str(patient_id),
        "external_event_id": f"phase3-{uuid4()}",
        "metric_type": "HEART_RATE",
        "numeric_value": "78",
        "metric_unit": "bpm",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": "VALID_REALTIME",
    }


def bearer_token(user, household, *, issued_at=None):
    token, _ = create_access_token(
        user_id=user.user_id,
        household_id=household.household_id,
        secret=JWT_SECRET,
        expires_minutes=30,
        now=issued_at,
    )
    return {"Authorization": f"Bearer {token}"}


def test_patient_can_upload_own_health_event(api_app, db_session):
    _, household, patient, user = make_household(
        db_session, "Patient A", "AAAA-BBBB"
    )
    db_session.commit()

    response = request(
        api_app,
        "POST",
        "/api/v1/health-events",
        headers=bearer_token(user, household),
        json=health_payload(patient.patient_id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["patient_id"] == str(patient.patient_id)


def test_patient_cannot_upload_another_patients_event(api_app, db_session):
    _, household_a, patient_a, user_a = make_household(
        db_session, "Patient A", "AAAA-BBBB"
    )
    _, _, patient_b, _ = make_household(
        db_session, "Patient B", "CCCC-DDDD"
    )
    db_session.commit()

    response = request(
        api_app,
        "POST",
        "/api/v1/health-events",
        headers=bearer_token(user_a, household_a),
        json=health_payload(patient_b.patient_id),
    )

    assert patient_a.patient_id != patient_b.patient_id
    assert response.status_code == 403
    assert response.json()["detail"] == "Patient identity mismatch."


def test_caregiver_cannot_upload_patient_health_event(api_app, db_session):
    admin, household, patient, _ = make_household(
        db_session, "Patient A", "AAAA-BBBB"
    )
    db_session.commit()

    response = request(
        api_app,
        "POST",
        "/api/v1/health-events",
        headers=bearer_token(admin, household),
        json=health_payload(patient.patient_id),
    )

    assert response.status_code == 403


def test_health_event_requires_authentication(api_app, db_session):
    _, _, patient, _ = make_household(
        db_session, "Patient A", "AAAA-BBBB"
    )
    db_session.commit()

    response = request(
        api_app,
        "POST",
        "/api/v1/health-events",
        json=health_payload(patient.patient_id),
    )

    assert response.status_code == 401


def test_health_event_rejects_invalid_token(api_app, db_session):
    _, _, patient, _ = make_household(
        db_session, "Patient A", "AAAA-BBBB"
    )
    db_session.commit()

    response = request(
        api_app,
        "POST",
        "/api/v1/health-events",
        headers={"Authorization": "Bearer invalid-token"},
        json=health_payload(patient.patient_id),
    )

    assert response.status_code == 401


def test_health_event_rejects_expired_token(api_app, db_session):
    _, household, patient, user = make_household(
        db_session, "Patient A", "AAAA-BBBB"
    )
    db_session.commit()

    response = request(
        api_app,
        "POST",
        "/api/v1/health-events",
        headers=bearer_token(
            user,
            household,
            issued_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
        json=health_payload(patient.patient_id),
    )

    assert response.status_code == 401

# Phase 3.3 — Data-access boundary

def test_cross_patient_rejection_has_no_downstream_side_effects(
    api_app,
    db_session,
):
    _, household_a, patient_a, user_a = make_household(
        db_session,
        "Patient A",
        "AAAA-BBBB",
    )
    _, _, patient_b, _ = make_household(
        db_session,
        "Patient B",
        "CCCC-DDDD",
    )
    db_session.commit()

    # Seed Patient B with one legitimate Critical reading.
    # A second matching Critical reading would be capable of confirming
    # the occurrence and producing an alert if it were accepted.
    first_event = create(
        db_session,
        patient_b,
        numeric_value="160",
        recorded_at=datetime(
            2026,
            7,
            17,
            5,
            0,
            0,
            tzinfo=timezone.utc,
        ),
    )

    tracker_before = db_session.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient_b.patient_id,
            ConditionTracker.active.is_(True),
        )
    )

    assert tracker_before is not None
    assert tracker_before.last_event_id == first_event.event_id

    health_events_before = db_session.scalar(
        select(func.count(HealthEvent.event_id))
    )
    evaluations_before = db_session.scalar(
        select(func.count(EventEvaluation.evaluation_id))
    )
    trackers_before = db_session.scalar(
        select(func.count(ConditionTracker.condition_tracker_id))
    )
    alerts_before = db_session.scalar(
        select(func.count(Alert.alert_id))
    )

    payload = health_payload(patient_b.patient_id)
    payload["numeric_value"] = "160"
    payload["recorded_at"] = datetime(
        2026,
        7,
        17,
        5,
        0,
        15,
        tzinfo=timezone.utc,
    ).isoformat()

    response = request(
        api_app,
        "POST",
        "/api/v1/health-events",
        headers=bearer_token(user_a, household_a),
        json=payload,
    )

    assert patient_a.patient_id != patient_b.patient_id
    assert response.status_code == 403
    assert response.json()["detail"] == "Patient identity mismatch."

    assert db_session.scalar(
        select(func.count(HealthEvent.event_id))
    ) == health_events_before

    assert db_session.scalar(
        select(func.count(EventEvaluation.evaluation_id))
    ) == evaluations_before

    assert db_session.scalar(
        select(func.count(ConditionTracker.condition_tracker_id))
    ) == trackers_before

    tracker_after = db_session.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient_b.patient_id,
            ConditionTracker.active.is_(True),
        )
    )

    assert tracker_after is not None
    assert tracker_after.last_event_id == first_event.event_id

    assert db_session.scalar(
        select(func.count(Alert.alert_id))
    ) == alerts_before
