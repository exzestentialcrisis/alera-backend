import os
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.households.model import Household
from app.patients.model import ElderlyPatient, Sex
from app.users.model import User, UserRole

ROOT = Path(__file__).resolve().parents[1]


def _validated_test_url() -> str | None:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        return None
    normal_url = os.getenv("DATABASE_URL")
    if normal_url and make_url(value).render_as_string(hide_password=False) == make_url(
        normal_url
    ).render_as_string(hide_password=False):
        raise pytest.UsageError("TEST_DATABASE_URL must not equal DATABASE_URL.")
    database_name = make_url(value).database or ""
    if "test" not in database_name.lower():
        raise pytest.UsageError(
            "TEST_DATABASE_URL database name must contain 'test' as a safety guard."
        )
    return value


def run_alembic(database_url: str, revision: str) -> None:
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        config = Config(str(ROOT / "alembic.ini"))
        command.upgrade(config, revision)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    value = _validated_test_url()
    if value is None:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value


@pytest.fixture(scope="session")
def integration_engine(test_database_url: str) -> Generator[Engine, None, None]:
    run_alembic(test_database_url, "head")
    engine = create_engine(test_database_url, echo=False)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(integration_engine: Engine) -> Generator[Session, None, None]:
    table_names = [
        "reminder_actions",
        "reminder_occurrences",
        "reminder_templates",
        "caregiver_push_devices",
        "patient_access_codes",
        "caregiver_patient_assignments",
        "alert_actions",
        "alerts",
        "condition_trackers",
        "event_evaluations",
        "health_events",
        "elderly_patients",
        "households",
        "users",
    ]
    with integration_engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE {', '.join(table_names)} RESTART IDENTITY CASCADE")
        )
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def patient(db_session: Session) -> ElderlyPatient:
    user = User(full_name="Test Patient", role=UserRole.ELDERLY_PATIENT)
    owner = User(full_name="Test Owner", role=UserRole.CARE_ADMIN)
    db_session.add_all([user, owner])
    db_session.flush()
    household = Household(
        created_by_user_id=owner.user_id,
        household_name="Test Household",
    )
    db_session.add(household)
    db_session.flush()
    patient = ElderlyPatient(
        user_id=user.user_id,
        household_id=household.household_id,
        birthdate=datetime(1950, 1, 1).date(),
        sex=Sex.OTHER,
        normal_hr_min=60,
        normal_hr_max=100,
        usual_spo2_min=95,
    )
    db_session.add(patient)
    db_session.commit()
    return patient


@pytest.fixture()
def event_payload(patient: ElderlyPatient) -> dict:
    return {
        "patient_id": patient.patient_id,
        "external_event_id": f"event-{uuid4()}",
        "metric_type": "HEART_RATE",
        "numeric_value": "78",
        "metric_unit": "bpm",
        "recorded_at": datetime(2026, 7, 17, 5, tzinfo=timezone.utc),
        "validation_status": "VALID_REALTIME",
    }


@pytest.fixture(autouse=True)
def disable_real_push_delivery(monkeypatch):
    # Tests explicitly inject mocked FCM transport/settings when exercising delivery.
    monkeypatch.setenv("FCM_ENABLED", "false")
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
