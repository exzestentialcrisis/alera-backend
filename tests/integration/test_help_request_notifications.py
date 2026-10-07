from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.devices.model import CaregiverPushDevice, PatientPushDevice
from app.help_requests import notification_service
from app.help_requests.model import HelpRequest, HelpRequestStatus
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.notifications.fcm import FCMSender
from app.users.model import AccountStatus, User, UserRole

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        fcm_enabled=True,
        firebase_project_id="alera-test",
        firebase_service_account_json=(
            '{"project_id":"alera-test",'
            '"client_email":"synthetic",'
            '"private_key":"synthetic"}'
        ),
    )


def _notification_context(db, patient):
    household = db.get(Household, patient.household_id)
    owner = db.get(User, household.created_by_user_id)
    patient_user = db.get(User, patient.user_id)

    assigned = User(
        full_name="Assigned Caregiver",
        role=UserRole.CAREGIVER,
        account_status=AccountStatus.ACTIVE,
    )
    unassigned = User(
        full_name="Unassigned Caregiver",
        role=UserRole.CAREGIVER,
        account_status=AccountStatus.ACTIVE,
    )
    db.add_all([assigned, unassigned])
    db.flush()

    request = HelpRequest(
        patient_id=patient.patient_id,
        client_action_id=uuid4(),
        message="Please help me",
        status=HelpRequestStatus.PENDING,
    )
    db.add_all(
        [
            CaregiverPatientAssignment(
                caregiver_user_id=assigned.user_id,
                patient_id=patient.patient_id,
                assigned_by_user_id=owner.user_id,
            ),
            CaregiverPushDevice(
                user_id=assigned.user_id,
                fcm_token="assigned-caregiver-token",
                platform="ANDROID",
            ),
            CaregiverPushDevice(
                user_id=unassigned.user_id,
                fcm_token="unassigned-caregiver-token",
                platform="ANDROID",
            ),
            PatientPushDevice(
                user_id=patient_user.user_id,
                fcm_token="patient-token",
                platform="ANDROID",
            ),
            request,
        ]
    )
    db.commit()

    patient_name = (
        (patient.nickname or "").strip()
        or (patient_user.full_name or "").strip()
        or "Your patient"
    )
    return request, patient_name


def _message(post: Mock) -> dict:
    return post.call_args.kwargs["json"]["message"]


def test_help_request_events_target_correct_devices_and_payloads(
    db_session,
    patient,
    monkeypatch,
):
    request, patient_name = _notification_context(
        db_session,
        patient,
    )

    monkeypatch.setattr(
        notification_service,
        "get_settings",
        _settings,
    )
    monkeypatch.setattr(
        FCMSender,
        "_access_token",
        lambda self: "mock-oauth",
    )
    post = Mock(
        return_value=httpx.Response(
            200,
            json={"name": "message"},
        )
    )
    monkeypatch.setattr(
        "app.notifications.fcm.httpx.post",
        post,
    )

    notification_service.deliver_help_request_notifications(
        db_session.get_bind(),
        {(request.help_request_id, "CREATED")},
    )

    post.assert_called_once()
    created = _message(post)
    assert created["token"] == "assigned-caregiver-token"
    assert created["notification"] == {
        "title": "Help requested",
        "body": f"{patient_name} requested assistance.",
    }
    assert created["data"] == {
        "type": "HELP_REQUEST",
        "event": "CREATED",
        "help_request_id": str(request.help_request_id),
        "patient_id": str(patient.patient_id),
        "status": "PENDING",
        "patient_display_name": patient_name,
        "title": "Help requested",
        "body": f"{patient_name} requested assistance.",
    }

    request.status = HelpRequestStatus.ACKNOWLEDGED
    db_session.commit()
    post.reset_mock()

    notification_service.deliver_help_request_notifications(
        db_session.get_bind(),
        {(request.help_request_id, "ACKNOWLEDGED")},
    )

    post.assert_called_once()
    acknowledged = _message(post)
    assert acknowledged["token"] == "patient-token"
    assert acknowledged["data"]["event"] == "ACKNOWLEDGED"
    assert acknowledged["data"]["status"] == "ACKNOWLEDGED"
    assert acknowledged["notification"] == {
        "title": "Help request acknowledged",
        "body": "Your caregiver knows that you need assistance.",
    }

    request.status = HelpRequestStatus.RESOLVED
    db_session.commit()
    post.reset_mock()

    notification_service.deliver_help_request_notifications(
        db_session.get_bind(),
        {(request.help_request_id, "RESOLVED")},
    )

    post.assert_called_once()
    resolved = _message(post)
    assert resolved["token"] == "patient-token"
    assert resolved["data"]["event"] == "RESOLVED"
    assert resolved["data"]["status"] == "RESOLVED"
    assert resolved["notification"] == {
        "title": "Help request resolved",
        "body": "Your caregiver marked your request as resolved.",
    }
