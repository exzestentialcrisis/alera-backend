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


NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)

JWT_SECRET = "sleep-trends-test-secret-that-is-long-enough"


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


def get_sleep_trends(
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
        f"/api/v1/patients/{patient_id}/sleep-trends",
        params=params,
        headers=api_app.state.test_headers,
    )


def add_daily_sleep(
    db,
    patient,
    *,
    activity_date,
    duration_seconds,
    activity_type=ActivityType.SLEEP,
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
        total_duration_seconds=duration_seconds,
        session_count=1,
    )

    db.add(daily)
    db.flush()

    return activity


def test_default_7d_returns_sleep_durations_and_summary(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=4),
        duration_seconds=21600,
    )

    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=2),
        duration_seconds=28800,
    )

    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date(),
        duration_seconds=25200,
    )

    db_session.commit()

    response = get_sleep_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["range"] == "7d"
    assert body["from_date"] == "2026-09-28"
    assert body["to_date"] == "2026-10-04"

    assert body["summary"] == {
        "latest_night": {
            "activity_date": "2026-10-04",
            "duration_seconds": 25200,
        },
        "average_duration_seconds": 25200.0,
        "longest_night": {
            "activity_date": "2026-10-02",
            "duration_seconds": 28800,
        },
        "shortest_night": {
            "activity_date": "2026-09-30",
            "duration_seconds": 21600,
        },
        "nights_with_data": 3,
    }

    assert body["points"] == [
        {
            "activity_date": "2026-09-30",
            "duration_seconds": 21600,
        },
        {
            "activity_date": "2026-10-02",
            "duration_seconds": 28800,
        },
        {
            "activity_date": "2026-10-04",
            "duration_seconds": 25200,
        },
    ]


def test_zero_duration_and_missing_nights_are_excluded(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=3),
        duration_seconds=0,
    )

    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=1),
        duration_seconds=27000,
    )

    db_session.commit()

    response = get_sleep_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["points"] == [
        {
            "activity_date": "2026-10-03",
            "duration_seconds": 27000,
        },
    ]

    assert body["summary"]["nights_with_data"] == 1
    assert body["summary"]["average_duration_seconds"] == 27000.0


def test_latest_night_is_newest_date_not_longest_duration(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=2),
        duration_seconds=32400,
    )

    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=1),
        duration_seconds=21600,
    )

    db_session.commit()

    response = get_sleep_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    summary = response.json()["summary"]

    assert summary["latest_night"] == {
        "activity_date": "2026-10-03",
        "duration_seconds": 21600,
    }

    assert summary["longest_night"] == {
        "activity_date": "2026-10-02",
        "duration_seconds": 32400,
    }


def test_30d_excludes_records_older_than_window(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=29),
        duration_seconds=25200,
    )

    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date() - timedelta(days=30),
        duration_seconds=28800,
    )

    db_session.commit()

    response = get_sleep_trends(
        client,
        api_app,
        patient.patient_id,
        trend_range="30d",
    )

    assert response.status_code == 200

    body = response.json()

    assert body["from_date"] == "2026-09-05"
    assert body["to_date"] == "2026-10-04"
    assert body["summary"]["nights_with_data"] == 1
    assert body["points"] == [
        {
            "activity_date": "2026-09-05",
            "duration_seconds": 25200,
        },
    ]


def test_non_sleep_activity_is_excluded(
    client,
    api_app,
    db_session,
    patient,
):
    add_daily_sleep(
        db_session,
        patient,
        activity_date=NOW.date(),
        duration_seconds=28800,
        activity_type=ActivityType.STEPS,
    )

    db_session.commit()

    response = get_sleep_trends(
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
    response = get_sleep_trends(
        client,
        api_app,
        patient.patient_id,
    )

    assert response.status_code == 200

    body = response.json()

    assert body["summary"] == {
        "latest_night": None,
        "average_duration_seconds": None,
        "longest_night": None,
        "shortest_night": None,
        "nights_with_data": 0,
    }

    assert body["points"] == []


def test_unsupported_range_returns_422(
    client,
    api_app,
    patient,
):
    response = get_sleep_trends(
        client,
        api_app,
        patient.patient_id,
        trend_range="24h",
    )

    assert response.status_code == 422

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

    response = get_sleep_trends(
        client,
        api_app,
        other_patient.patient_id,
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Patient not found.",
    }


def test_patient_account_cannot_read_sleep_trends(
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
        f"/api/v1/patients/{patient.patient_id}/sleep-trends",
        headers={
            "Authorization": f"Bearer {token}",
        },
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Caregiver access required.",
    }

