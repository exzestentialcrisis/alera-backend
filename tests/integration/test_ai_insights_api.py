from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.core.config import Settings
from app.db.database import get_db
from app.households.model import Household
from app.main import create_app
from app.users.model import User

pytestmark = pytest.mark.integration

JWT_SECRET = "caregiver-insights-test-secret-that-is-long-enough"


@pytest.fixture()
def api_app(db_session, patient):
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

    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
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
    return TestClient(api_app, raise_server_exceptions=False)


def test_caregiver_can_get_fact_grounded_insights(client, api_app, patient):
    response = client.get(
        f"/api/v1/patients/{patient.patient_id}/caregiver-insights",
        headers=api_app.state.test_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == str(patient.patient_id)
    assert body["range"] == "7d"
    assert body["source"] == "fallback"
    assert body["items"]
    assert body["disclaimer"]


def test_patient_outside_actor_scope_returns_404(client, api_app):
    response = client.get(
        f"/api/v1/patients/{uuid4()}/caregiver-insights",
        headers=api_app.state.test_headers,
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Patient not found."}


def test_caregiver_insights_requires_authentication(client, patient):
    response = client.get(
        f"/api/v1/patients/{patient.patient_id}/caregiver-insights",
    )

    assert response.status_code == 401
