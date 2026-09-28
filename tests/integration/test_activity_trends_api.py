from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.activity.service as activity_service
from app.activity.model import (
    ActivityDailyData,
    ActivityData,
    ActivityType,
)
from app.auth.security import create_access_token
from app.core.config import Settings
from app.db.database import get_db
from app.households.model import Household
from app.main import create_app
from app.patients.model import ElderlyPatient, Sex
from app.users.model import User, UserRole

pytestmark = pytest.mark.integration


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

JWT_SECRET = "activity-trends-test-secret-that-is-long-enough"


@pytest.fixture()
def api_app(db_session, patient, monkeypatch):
    monkeypatch.setattr(
        activity_service,
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

    app.state.test_headers = {
        "Authorization": f"Bearer {token}",
    }

    return app


@pytest.fixture()
def client(api_app):
    return TestClient(
        api_app,
        raise_server_exceptions=False,
    )


def get_activity_trends(
    client,
    api_app,
    patient_id,
    *,
    trend_range=None,
):
    params = {}

    if trend_range is not None:
        params["range"] = trend_range

    return client.get(
        f"/api/v1/patients/{patient_id}/activity-trends",
        params=params,
        headers=api_app.state.test_headers,
    )


def add_daily_steps(
    db,
    patient,
    *,
    activity_date,
    total_steps,
    activity_type=ActivityType.STEPS,
):
    activity = ActivityData(
        patient_id=patient.patient_id,
        activity_date=activity_date,
        activity_type=activity_type,
    )

    db.add(activity)
    db.flush()

    daily = ActivityDailyData(
        activity_data_id=activity.activity_data_id,
        total_steps=total_steps,
        total_duration_seconds=0,
        session_count=0,
    )

    db.add(daily)
    db.flush()

    return activity


def test_default_7d_returns_daily_steps_and_summary(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=4),
        total_steps=1200,
    )

    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=2),
        total_steps=6000,
    )

    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date(),
        total_steps=3000,
    )

    db_session.commit()

    response = get_activity_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["range"] == "7d"
    assert body["from_date"] == "2026-09-12"
    assert body["to_date"] == "2026-09-18"

    assert body["summary"] == {
        "average_steps_per_day": 3400.0,
        "highest_day": {
            "activity_date": "2026-09-16",
            "total_steps": 6000,
        },
        "lowest_day": {
            "activity_date": "2026-09-14",
            "total_steps": 1200,
        },
        "days_with_data": 3,
    }

    assert body["points"] == [
        {
            "activity_date": "2026-09-14",
            "total_steps": 1200,
        },
        {
            "activity_date": "2026-09-16",
            "total_steps": 6000,
        },
        {
            "activity_date": "2026-09-18",
            "total_steps": 3000,
        },
    ]


def test_missing_dates_and_null_steps_are_not_returned_as_zero(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=3),
        total_steps=None,
    )

    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=1),
        total_steps=2500,
    )

    db_session.commit()

    response = get_activity_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["points"] == [
        {
            "activity_date": "2026-09-17",
            "total_steps": 2500,
        },
    ]

    assert body["summary"]["days_with_data"] == 1
    assert body["summary"]["average_steps_per_day"] == 2500.0


def test_30d_excludes_records_older_than_the_window(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=29),
        total_steps=1000,
    )

    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=30),
        total_steps=9000,
    )

    db_session.commit()

    response = get_activity_trends(
        client,
        api_app,
        patient.patient_id,
        trend_range="30d",
    )

    assert response.status_code == 200

    body = response.json()

    assert body["range"] == "30d"
    assert body["from_date"] == "2026-08-20"
    assert body["to_date"] == "2026-09-18"
    assert body["summary"]["days_with_data"] == 1
    assert body["points"] == [
        {
            "activity_date": "2026-08-20",
            "total_steps": 1000,
        },
    ]


def test_non_step_activity_is_excluded(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date(),
        total_steps=4000,
        activity_type=ActivityType.WALKING,
    )

    db_session.commit()

    response = get_activity_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200
    assert response.json()["points"] == []


def test_empty_period_returns_null_summary_values(
    client,
    api_app,
    patient,
):
    response = get_activity_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["summary"] == {
        "average_steps_per_day": None,
        "highest_day": None,
        "lowest_day": None,
        "days_with_data": 0,
    }

    assert body["points"] == []


def test_unsupported_range_returns_422(
    client,
    api_app,
    patient,
):
    response = get_activity_trends(
        client,
        api_app,
        patient.patient_id,
        trend_range="24h",
    )

    assert response.status_code == 422

def test_recorded_zero_steps_remains_real_data(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_steps(
        db_session,
        patient,
        activity_date=NOW.date(),
        total_steps=0,
    )

    db_session.commit()

    response = get_activity_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["points"] == [
        {
            "activity_date": "2026-09-18",
            "total_steps": 0,
        },
    ]

    assert body["summary"] == {
        "average_steps_per_day": 0.0,
        "highest_day": {
            "activity_date": "2026-09-18",
            "total_steps": 0,
        },
        "lowest_day": {
            "activity_date": "2026-09-18",
            "total_steps": 0,
        },
        "days_with_data": 1,
    }


def test_patient_outside_actor_scope_returns_404(
    client,
    api_app,
    db_session,
):
    other_admin = User(
        full_name="Other Care Admin",
        role=UserRole.CARE_ADMIN,
    )

    other_user = User(
        full_name="Other Patient",
        role=UserRole.ELDERLY_PATIENT,
    )

    db_session.add_all([other_admin, other_user])
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

    response = get_activity_trends(
        client,
        api_app,
        other_patient.patient_id,
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Patient not found.",
    }


def test_patient_account_cannot_read_activity_trends(
    client,
    api_app,
    db_session,
    patient,
):
    patient_user = db_session.get(
        User,
        patient.user_id,
    )

    token, _ = create_access_token(
        user_id=patient_user.user_id,
        household_id=patient.household_id,
        secret=JWT_SECRET,
        expires_minutes=30,
    )

    response = client.get(
        f"/api/v1/patients/{patient.patient_id}/activity-trends",
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Caregiver access required.",
    }

