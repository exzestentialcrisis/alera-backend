from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import app.health_events.service as trend_service
from app.auth.security import create_access_token
from app.core.config import Settings
from app.db.database import get_db
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    EventEvaluation,
    MonitoringState,
)
from app.health_events.model import (
    HealthEvent,
    MetricType,
    ValidationStatus,
)
from app.households.model import Household
from app.main import create_app
from app.patients.model import ElderlyPatient, Sex
from app.users.model import User, UserRole

pytestmark = pytest.mark.integration


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

JWT_SECRET = "vital-trends-test-secret-that-is-long-enough"


@pytest.fixture()
def api_app(db_session, patient, monkeypatch):
    # Freeze trend windows so tests do not depend on the real clock.
    monkeypatch.setattr(
        trend_service,
        "utc_now",
        lambda: NOW,
    )

    app = create_app(
        Settings(
            environment="testing",
            database_url=None,
            alera_jwt_secret=JWT_SECRET,
        )
    )

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db

    household = db_session.get(
        Household,
        patient.household_id,
    )

    owner = db_session.get(
        User,
        household.created_by_user_id,
    )

    token, _ = create_access_token(
        user_id=owner.user_id,
        household_id=household.household_id,
        secret=JWT_SECRET,
        expires_minutes=30,
    )

    app.state.test_headers = {"Authorization": f"Bearer {token}"}

    return app


@pytest.fixture()
def client(api_app):
    return TestClient(
        api_app,
        raise_server_exceptions=False,
    )


def get_trends(
    client,
    api_app,
    patient_id,
    *,
    metric_type="HEART_RATE",
    trend_range="24h",
):
    return client.get(
        f"/api/v1/patients/{patient_id}/vital-trends",
        params={
            "metric_type": metric_type,
            "range": trend_range,
        },
        headers=api_app.state.test_headers,
    )


def add_event(
    db,
    patient,
    *,
    metric_type,
    value,
    recorded_at,
    severity=None,
    condition_key=None,
    validation_status=ValidationStatus.VALID_REALTIME,
):
    unit = "bpm" if metric_type == MetricType.HEART_RATE else "percent"

    event = HealthEvent(
        patient_id=patient.patient_id,
        metric_type=metric_type,
        numeric_value=Decimal(str(value)),
        metric_unit=unit,
        recorded_at=recorded_at,
        validation_status=validation_status,
        raw_payload={},
    )

    db.add(event)
    db.flush()

    if severity is not None and condition_key is not None:
        evaluation = EventEvaluation(
            event_id=event.event_id,
            condition_key=condition_key,
            threshold_value_used=None,
            threshold_met=severity != EvaluationSeverity.INFO,
            persistence_met=False,
            previous_state=MonitoringState.STABLE,
            new_state=MonitoringState.STABLE,
            severity=severity,
            evaluation_reason="Vital trend API test.",
        )

        db.add(evaluation)

    db.flush()

    return event


def test_hr_24h_aggregates_into_hourly_buckets(
    client,
    api_app,
    db_session,
    patient,
):
    # These three readings belong to the same rolling hour.
    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=80,
        recorded_at=NOW
        - timedelta(
            hours=2,
            minutes=50,
        ),
        severity=EvaluationSeverity.INFO,
        condition_key=ConditionKey.HR_NORMAL,
    )

    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=120,
        recorded_at=NOW
        - timedelta(
            hours=2,
            minutes=40,
        ),
        severity=EvaluationSeverity.WARNING,
        condition_key=ConditionKey.HR_HIGH,
    )

    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=160,
        recorded_at=NOW
        - timedelta(
            hours=2,
            minutes=30,
        ),
        severity=EvaluationSeverity.CRITICAL,
        condition_key=ConditionKey.HR_HIGH,
    )

    # Newer reading belongs to a different bucket.
    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=74,
        recorded_at=NOW - timedelta(minutes=30),
        severity=EvaluationSeverity.INFO,
        condition_key=ConditionKey.HR_NORMAL,
    )

    db_session.commit()

    response = get_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["range"] == "24h"
    assert body["resolution"] == "1h"

    assert body["summary"] == {
        "latest": 74.0,
        "average": 108.5,
        "minimum": 74.0,
        "maximum": 160.0,
        "reading_count": 4,
    }

    assert len(body["points"]) == 2

    first_bucket = body["points"][0]

    assert first_bucket["value"] == 120.0
    assert first_bucket["minimum"] == 80.0
    assert first_bucket["maximum"] == 160.0
    assert first_bucket["reading_count"] == 3
    assert first_bucket["severity"] == "CRITICAL"

    second_bucket = body["points"][1]

    assert second_bucket["value"] == 74.0
    assert second_bucket["reading_count"] == 1
    assert second_bucket["severity"] == "INFO"


def test_spo2_7d_aggregates_into_daily_bucket(
    client,
    api_app,
    db_session,
    patient,
):
    add_event(
        db_session,
        patient,
        metric_type=MetricType.SPO2,
        value=97,
        recorded_at=NOW
        - timedelta(
            days=2,
            hours=-1,
        ),
        severity=EvaluationSeverity.INFO,
        condition_key=ConditionKey.SPO2_NORMAL,
    )

    add_event(
        db_session,
        patient,
        metric_type=MetricType.SPO2,
        value=84,
        recorded_at=NOW
        - timedelta(
            days=2,
            hours=-2,
        ),
        severity=EvaluationSeverity.CRITICAL,
        condition_key=ConditionKey.SPO2_LOW,
    )

    db_session.commit()

    response = get_trends(
        client,
        api_app,
        patient.patient_id,
        metric_type="SPO2",
        trend_range="7d",
    )

    assert response.status_code == 200

    body = response.json()

    assert body["range"] == "7d"
    assert body["resolution"] == "1d"

    assert body["summary"]["latest"] == 84.0
    assert body["summary"]["average"] == 90.5
    assert body["summary"]["minimum"] == 84.0
    assert body["summary"]["maximum"] == 97.0
    assert body["summary"]["reading_count"] == 2

    assert len(body["points"]) == 1

    point = body["points"][0]

    assert point["value"] == 90.5
    assert point["minimum"] == 84.0
    assert point["maximum"] == 97.0
    assert point["reading_count"] == 2
    assert point["severity"] == "CRITICAL"


def test_latest_is_latest_raw_reading_not_bucket_average(
    client,
    api_app,
    db_session,
    patient,
):
    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=140,
        recorded_at=NOW - timedelta(minutes=40),
        severity=EvaluationSeverity.WARNING,
        condition_key=ConditionKey.HR_HIGH,
    )

    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=80,
        recorded_at=NOW - timedelta(minutes=20),
        severity=EvaluationSeverity.INFO,
        condition_key=ConditionKey.HR_NORMAL,
    )

    db_session.commit()

    response = get_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["summary"]["latest"] == 80.0

    # Both readings are in one bucket:
    # (140 + 80) / 2 = 110
    assert body["points"][0]["value"] == 110.0


def test_no_readings_returns_empty_trend(
    client,
    api_app,
    patient,
):
    response = get_trends(
        client,
        api_app,
        patient.patient_id,
        metric_type="SPO2",
    )

    assert response.status_code == 200

    body = response.json()

    assert body["resolution"] == "1h"

    assert body["summary"] == {
        "latest": None,
        "average": None,
        "minimum": None,
        "maximum": None,
        "reading_count": 0,
    }

    assert body["points"] == []


def test_invalid_and_outside_range_events_are_excluded(
    client,
    api_app,
    db_session,
    patient,
):
    # Valid reading inside the window.
    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=75,
        recorded_at=NOW - timedelta(hours=1),
        severity=EvaluationSeverity.INFO,
        condition_key=ConditionKey.HR_NORMAL,
    )

    # Inside the window, but explicitly invalid.
    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=190,
        recorded_at=NOW - timedelta(minutes=30),
        validation_status=ValidationStatus.INVALID,
    )

    # Valid, but older than 24 hours.
    add_event(
        db_session,
        patient,
        metric_type=MetricType.HEART_RATE,
        value=88,
        recorded_at=NOW - timedelta(hours=25),
        severity=EvaluationSeverity.INFO,
        condition_key=ConditionKey.HR_NORMAL,
    )

    db_session.commit()

    response = get_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["summary"]["reading_count"] == 1
    assert body["summary"]["latest"] == 75.0
    assert body["summary"]["minimum"] == 75.0
    assert body["summary"]["maximum"] == 75.0

    assert len(body["points"]) == 1
    assert body["points"][0]["value"] == 75.0


@pytest.mark.parametrize(
    ("trend_range", "expected_resolution"),
    [
        ("7d", "1d"),
        ("30d", "1d"),
    ],
)
def test_longer_ranges_use_daily_resolution(
    client,
    api_app,
    patient,
    trend_range,
    expected_resolution,
):
    response = get_trends(
        client,
        api_app,
        patient.patient_id,
        trend_range=trend_range,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["range"] == trend_range
    assert body["resolution"] == expected_resolution


def test_unsupported_metric_returns_422(
    client,
    api_app,
    patient,
):
    response = get_trends(
        client,
        api_app,
        patient.patient_id,
        metric_type="BATTERY_LEVEL",
    )

    assert response.status_code == 422

    assert response.json() == {
        "detail": ("Vital trends are only available " "for HEART_RATE and SPO2.")
    }


def test_patient_outside_actor_scope_returns_404(
    client,
    api_app,
    db_session,
    patient,
):
    other_admin = User(
        full_name="Other Care Admin",
        role=UserRole.CARE_ADMIN,
    )

    other_user = User(
        full_name="Other Patient",
        role=UserRole.ELDERLY_PATIENT,
    )

    db_session.add_all(
        [
            other_admin,
            other_user,
        ]
    )

    db_session.flush()

    other_household = Household(
        created_by_user_id=other_admin.user_id,
        household_name="Other Household",
    )

    db_session.add(other_household)
    db_session.flush()

    other_patient = ElderlyPatient(
        user_id=other_user.user_id,
        household_id=other_household.household_id,
        birthdate=datetime(1950, 1, 1).date(),
        sex=Sex.OTHER,
        normal_hr_min=60,
        normal_hr_max=100,
        usual_spo2_min=95,
    )

    db_session.add(other_patient)
    db_session.commit()

    response = get_trends(
        client,
        api_app,
        other_patient.patient_id,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Patient not found."}
