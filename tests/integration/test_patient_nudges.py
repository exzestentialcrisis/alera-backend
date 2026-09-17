import asyncio
from unittest.mock import Mock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.auth.security import create_access_token
from app.core.config import Settings
from app.db.database import get_db
from app.devices.model import PatientPushDevice
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.main import create_app
from app.notifications.fcm import FCMSender
from app.nudges import notification_service
from app.nudges.model import PatientNudge
from app.users.model import User, UserRole


pytestmark = pytest.mark.integration
SECRET = "patient-nudge-test-secret-that-is-long-enough"


@pytest.fixture()
def nudge_app(db_session):
    app = create_app(Settings(_env_file=None, alera_jwt_secret=SECRET))

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return app


def request(app, method, path, *, user, household_id, json):
    token, _ = create_access_token(
        user_id=user.user_id,
        household_id=household_id,
        secret=SECRET,
        expires_minutes=30,
    )

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(
                method,
                path,
                headers={"Authorization": f"Bearer {token}"},
                json=json,
            )

    return asyncio.run(send())


def actors(db, patient):
    household = db.get(Household, patient.household_id)
    owner = db.get(User, household.created_by_user_id)
    patient_user = db.get(User, patient.user_id)
    assigned = User(full_name="Assigned", role=UserRole.CAREGIVER)
    unassigned = User(full_name="Unassigned", role=UserRole.CAREGIVER)
    db.add_all([assigned, unassigned])
    db.flush()
    db.add(
        CaregiverPatientAssignment(
            caregiver_user_id=assigned.user_id,
            patient_id=patient.patient_id,
            assigned_by_user_id=owner.user_id,
        )
    )
    db.commit()
    return household, owner, patient_user, assigned, unassigned


def payload(action_id=None, nudge_type="DRINK_WATER"):
    return {
        "nudge_type": nudge_type,
        "client_action_id": str(action_id or uuid4()),
    }


def test_assigned_caregiver_sends_idempotent_nudge(
    nudge_app, db_session, patient, monkeypatch
):
    household, _owner, _patient_user, assigned, _unassigned = actors(
        db_session, patient
    )
    delivered = Mock()
    monkeypatch.setattr(
        "app.nudges.notification_service.deliver_patient_nudges", delivered
    )
    action_id = uuid4()

    first = request(
        nudge_app,
        "POST",
        f"/api/v1/patients/{patient.patient_id}/nudges",
        user=assigned,
        household_id=household.household_id,
        json=payload(action_id),
    )
    assert first.status_code == 201
    assert first.json()["idempotent"] is False
    delivered.assert_called_once()
    nudge_id = UUID(first.json()["nudge_id"])

    delivered.reset_mock()
    replay = request(
        nudge_app,
        "POST",
        f"/api/v1/patients/{patient.patient_id}/nudges",
        user=assigned,
        household_id=household.household_id,
        json=payload(action_id),
    )
    assert replay.status_code == 201
    assert replay.json()["nudge_id"] == str(nudge_id)
    assert replay.json()["idempotent"] is True
    delivered.assert_not_called()
    assert len(db_session.scalars(select(PatientNudge)).all()) == 1


def test_nudge_enforces_role_assignment_and_action_identity(
    nudge_app, db_session, patient
):
    household, owner, patient_user, assigned, unassigned = actors(db_session, patient)
    path = f"/api/v1/patients/{patient.patient_id}/nudges"
    action_id = uuid4()

    assert request(
        nudge_app,
        "POST",
        path,
        user=unassigned,
        household_id=household.household_id,
        json=payload(),
    ).status_code == 404
    for actor in (owner, patient_user):
        assert request(
            nudge_app,
            "POST",
            path,
            user=actor,
            household_id=household.household_id,
            json=payload(),
        ).status_code == 403

    assert request(
        nudge_app,
        "POST",
        path,
        user=assigned,
        household_id=household.household_id,
        json=payload(action_id),
    ).status_code == 201
    assert request(
        nudge_app,
        "POST",
        path,
        user=assigned,
        household_id=household.household_id,
        json=payload(action_id, "TAKE_MEDICATION"),
    ).status_code == 409


def test_patient_device_receives_exact_nudge_payload(
    db_session, patient, monkeypatch
):
    household, _owner, patient_user, assigned, _unassigned = actors(db_session, patient)
    nudge = PatientNudge(
        patient_id=patient.patient_id,
        sent_by_user_id=assigned.user_id,
        nudge_type="CHECK_BLOOD_PRESSURE",
        client_action_id=uuid4(),
    )
    device = PatientPushDevice(
        user_id=patient_user.user_id,
        fcm_token="patient-device-token",
        platform="ANDROID",
    )
    db_session.add_all([nudge, device])
    db_session.commit()

    settings = Settings(
        _env_file=None,
        fcm_enabled=True,
        firebase_project_id="alera-test",
        firebase_service_account_json=(
            '{"project_id":"alera-test","client_email":"synthetic",'
            '"private_key":"synthetic"}'
        ),
    )
    monkeypatch.setattr(notification_service, "get_settings", lambda: settings)
    monkeypatch.setattr(FCMSender, "_access_token", lambda self: "mock-oauth")
    post = Mock(return_value=httpx.Response(200, json={"name": "message"}))
    monkeypatch.setattr("app.notifications.fcm.httpx.post", post)

    notification_service.deliver_patient_nudges(
        db_session.get_bind(), {nudge.nudge_id}
    )

    message = post.call_args.kwargs["json"]["message"]
    assert message["token"] == "patient-device-token"
    assert message["notification"]["title"] == "Please check your blood pressure"
    assert message["data"] == {
        "type": "NUDGE",
        "nudge_id": str(nudge.nudge_id),
        "patient_id": str(patient.patient_id),
        "nudge_type": "CHECK_BLOOD_PRESSURE",
    }
