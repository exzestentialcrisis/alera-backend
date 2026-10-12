import importlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from app.auth.security import create_access_token
from app.core.config import Settings
from app.db.database import get_db
from app.event_evaluations.model import EventEvaluation
from app.health_events.errors import HealthEventConstraintError
from app.health_events.model import HealthEvent
from app.health_events.schema import HealthEventCreate
import app.health_events.service as ingestion_service
from app.main import create_app

pytestmark = pytest.mark.integration
JWT_SECRET = "api-errors-test-secret-that-is-long-and-random-enough"

@pytest.fixture()
def client(db_session, patient):
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

    token, _ = create_access_token(
        user_id=patient.user_id,
        household_id=patient.household_id,
        secret=JWT_SECRET,
        expires_minutes=30,
    )

    client = TestClient(app, raise_server_exceptions=False)
    client.headers.update({
        "Authorization": f"Bearer {token}",
    })

    return client


def json_payload(event_payload):
    data = dict(event_payload)
    data["patient_id"] = str(data["patient_id"])
    data["recorded_at"] = data["recorded_at"].isoformat()
    return data


def test_mismatched_patient_returns_403(client, event_payload):
    payload = json_payload(event_payload)
    payload["patient_id"] = str(uuid4())

    response = client.post(
        "/api/v1/health-events",
        json=payload,
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Patient identity mismatch.",
    }


def test_identical_duplicate_is_idempotent(client, db_session, event_payload):
    payload = json_payload(event_payload)
    first = client.post("/api/v1/health-events", json=payload)
    second = client.post("/api/v1/health-events", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["event_id"] == first.json()["event_id"]
    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 1
    assert db_session.scalar(select(func.count(EventEvaluation.evaluation_id))) == 1


def test_conflicting_duplicate_returns_409(client, event_payload):
    payload = json_payload(event_payload)
    assert client.post("/api/v1/health-events", json=payload).status_code == 200
    payload["numeric_value"] = "79"
    response = client.post("/api/v1/health-events", json=payload)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "external_event_id is already used by a different event."
    }


def test_session_is_usable_after_failed_request(client, db_session, event_payload):
    payload = json_payload(event_payload)
    payload["patient_id"] = str(uuid4())
    assert client.post("/api/v1/health-events", json=payload).status_code == 403
    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 0


def test_unexpected_exception_returns_sanitized_500(
    client,
    event_payload,
    monkeypatch,
):
    router_module = importlib.import_module("app.health_events.router")

    def fail(*_args, **_kwargs):
        raise RuntimeError("secret table and SQL details")

    monkeypatch.setattr(router_module, "create_health_event", fail)
    response = client.post("/api/v1/health-events", json=json_payload(event_payload))
    assert response.status_code == 500
    assert response.text == "Internal Server Error"


def test_foreign_key_race_becomes_controlled_constraint_error(
    integration_engine,
    db_session,
    patient,
    event_payload,
    monkeypatch,
):
    original_find = ingestion_service._find_external_event
    deleted = False

    def delete_patient_then_find(db, external_event_id):
        nonlocal deleted
        if not deleted:
            deleted = True
            other = sessionmaker(bind=integration_engine)()
            try:
                concurrent_patient = other.get(type(patient), patient.patient_id)
                other.delete(concurrent_patient)
                other.commit()
            finally:
                other.close()
        return original_find(db, external_event_id)

    monkeypatch.setattr(
        ingestion_service,
        "_find_external_event",
        delete_patient_then_find,
    )
    with pytest.raises(HealthEventConstraintError):
        ingestion_service.create_health_event(
            db_session,
            HealthEventCreate.model_validate(event_payload),
        )
    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 0
