import asyncio
from unittest.mock import Mock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.auth.security import create_access_token
from app.core.config import Settings
from app.db.database import get_db
from app.help_requests.model import HelpRequest, HelpRequestNote
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.main import create_app
from app.users.model import User, UserRole

pytestmark = pytest.mark.integration
SECRET = "help-request-test-secret-that-is-long-enough"


@pytest.fixture()
def help_request_app(db_session):
    app = create_app(
        Settings(
            _env_file=None,
            alera_jwt_secret=SECRET,
        )
    )

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return app


def request(
    app,
    method,
    path,
    *,
    user,
    household_id,
    json=None,
):
    token, _ = create_access_token(
        user_id=user.user_id,
        household_id=household_id,
        secret=SECRET,
        expires_minutes=30,
    )

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {token}",
                },
                json=json,
            )

    return asyncio.run(send())


def patient_identity(db, patient):
    household = db.get(Household, patient.household_id)
    patient_user = db.get(User, patient.user_id)
    owner = db.get(User, household.created_by_user_id)
    return household, patient_user, owner


def payload(action_id=None, message=None):
    return {
        "client_action_id": str(action_id or uuid4()),
        "message": message,
    }


def test_patient_creates_and_reads_active_help_request(
    help_request_app,
    db_session,
    patient,
):
    household, patient_user, _owner = patient_identity(
        db_session,
        patient,
    )
    action_id = uuid4()

    empty = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests/active",
        user=patient_user,
        household_id=household.household_id,
    )
    assert empty.status_code == 200
    assert empty.json() is None

    created = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=payload(action_id, "  I need assistance  "),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["patient_id"] == str(patient.patient_id)
    assert body["client_action_id"] == str(action_id)
    assert body["status"] == "PENDING"
    assert body["message"] == "I need assistance"
    assert body["idempotent"] is False

    active = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests/active",
        user=patient_user,
        household_id=household.household_id,
    )
    assert active.status_code == 200
    assert active.json()["help_request_id"] == body["help_request_id"]

    stored = db_session.scalar(
        select(HelpRequest).where(
            HelpRequest.help_request_id
            == UUID(body["help_request_id"])
        )
    )
    assert stored is not None
    assert stored.patient_id == patient.patient_id
    assert stored.message == "I need assistance"


def test_same_action_replays_but_new_unresolved_request_conflicts(
    help_request_app,
    db_session,
    patient,
):
    household, patient_user, _owner = patient_identity(
        db_session,
        patient,
    )
    action_id = uuid4()
    body = payload(action_id, "Please call me")

    first = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=body,
    )
    assert first.status_code == 201

    replay = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=body,
    )
    assert replay.status_code == 201
    assert replay.json()["help_request_id"] == first.json()["help_request_id"]
    assert replay.json()["idempotent"] is True

    changed_replay = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=payload(action_id, "Different meaning"),
    )
    assert changed_replay.status_code == 409

    another_request = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=payload(),
    )
    assert another_request.status_code == 409

    assert len(
        db_session.scalars(select(HelpRequest)).all()
    ) == 1


def test_identity_comes_from_patient_token(
    help_request_app,
    db_session,
    patient,
):
    household, patient_user, owner = patient_identity(
        db_session,
        patient,
    )

    spoofed = payload()
    spoofed["patient_id"] = str(uuid4())

    response = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=spoofed,
    )
    assert response.status_code == 422

    caregiver = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=owner,
        household_id=household.household_id,
        json=payload(),
    )
    assert caregiver.status_code == 403

    caregiver_read = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests/active",
        user=owner,
        household_id=household.household_id,
    )
    assert caregiver_read.status_code == 403

    assert db_session.scalar(select(HelpRequest)) is None


def test_blank_message_normalizes_to_null(
    help_request_app,
    db_session,
    patient,
):
    household, patient_user, _owner = patient_identity(
        db_session,
        patient,
    )

    response = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=payload(message="   "),
    )

    assert response.status_code == 201
    assert response.json()["message"] is None



def setup_caregivers(db, patient):
    household, _patient_user, owner = patient_identity(db, patient)
    assigned = User(
        full_name="Assigned Help Caregiver",
        role=UserRole.CAREGIVER,
    )
    unassigned = User(
        full_name="Unassigned Help Caregiver",
        role=UserRole.CAREGIVER,
    )
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
    return household, assigned, unassigned


def create_patient_help_request(
    app,
    db,
    patient,
    *,
    message="Please help me",
):
    household, patient_user, _owner = patient_identity(db, patient)
    response = request(
        app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=payload(message=message),
    )
    assert response.status_code == 201
    return response.json(), household, patient_user


def test_assigned_caregiver_lists_and_reads_help_request(
    help_request_app,
    db_session,
    patient,
):
    created, household, _patient_user = create_patient_help_request(
        help_request_app,
        db_session,
        patient,
    )
    _household, assigned, _unassigned = setup_caregivers(
        db_session,
        patient,
    )

    listed = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests",
        user=assigned,
        household_id=household.household_id,
    )

    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    assert (
        body["items"][0]["help_request_id"]
        == created["help_request_id"]
    )

    patient_user = db_session.get(User, patient.user_id)
    expected_name = patient.nickname or patient_user.full_name
    assert body["items"][0]["patient_display_name"] == expected_name

    detail = request(
        help_request_app,
        "GET",
        f"/api/v1/help-requests/{created['help_request_id']}",
        user=assigned,
        household_id=household.household_id,
    )

    assert detail.status_code == 200
    assert detail.json()["message"] == "Please help me"
    assert detail.json()["patient_display_name"] == expected_name


def test_unassigned_caregiver_cannot_discover_help_request(
    help_request_app,
    db_session,
    patient,
):
    created, household, _patient_user = create_patient_help_request(
        help_request_app,
        db_session,
        patient,
    )
    _household, _assigned, unassigned = setup_caregivers(
        db_session,
        patient,
    )

    listed = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests",
        user=unassigned,
        household_id=household.household_id,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 0
    assert listed.json()["items"] == []

    detail = request(
        help_request_app,
        "GET",
        f"/api/v1/help-requests/{created['help_request_id']}",
        user=unassigned,
        household_id=household.household_id,
    )
    assert detail.status_code == 404

    acknowledge = request(
        help_request_app,
        "POST",
        (
            f"/api/v1/help-requests/"
            f"{created['help_request_id']}/acknowledge"
        ),
        user=unassigned,
        household_id=household.household_id,
    )
    assert acknowledge.status_code == 404

    resolve = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{created['help_request_id']}/resolve",
        user=unassigned,
        household_id=household.household_id,
    )
    assert resolve.status_code == 404


def test_caregiver_acknowledges_and_resolves_help_request(
    help_request_app,
    db_session,
    patient,
):
    created, household, patient_user = create_patient_help_request(
        help_request_app,
        db_session,
        patient,
    )
    _household, assigned, _unassigned = setup_caregivers(
        db_session,
        patient,
    )
    request_id = created["help_request_id"]

    acknowledged = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/acknowledge",
        user=assigned,
        household_id=household.household_id,
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "ACKNOWLEDGED"
    assert acknowledged.json()["idempotent"] is False
    assert (
        acknowledged.json()["acknowledged_by_user_id"]
        == str(assigned.user_id)
    )
    assert acknowledged.json()["acknowledged_at"] is not None

    replayed_acknowledgement = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/acknowledge",
        user=assigned,
        household_id=household.household_id,
    )
    assert replayed_acknowledgement.status_code == 200
    assert replayed_acknowledgement.json()["idempotent"] is True

    still_active = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests/active",
        user=patient_user,
        household_id=household.household_id,
    )
    assert still_active.status_code == 200
    assert still_active.json()["status"] == "ACKNOWLEDGED"

    resolved = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/resolve",
        user=assigned,
        household_id=household.household_id,
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"
    assert resolved.json()["idempotent"] is False
    assert (
        resolved.json()["resolved_by_user_id"]
        == str(assigned.user_id)
    )
    assert resolved.json()["resolved_at"] is not None

    replayed_resolution = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/resolve",
        user=assigned,
        household_id=household.household_id,
    )
    assert replayed_resolution.status_code == 200
    assert replayed_resolution.json()["idempotent"] is True

    late_acknowledgement = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/acknowledge",
        user=assigned,
        household_id=household.household_id,
    )
    assert late_acknowledgement.status_code == 409

    no_active_request = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests/active",
        user=patient_user,
        household_id=household.household_id,
    )
    assert no_active_request.status_code == 200
    assert no_active_request.json() is None

    default_list = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests",
        user=assigned,
        household_id=household.household_id,
    )
    assert default_list.status_code == 200
    assert default_list.json()["total"] == 0

    resolved_history = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests?status=RESOLVED",
        user=assigned,
        household_id=household.household_id,
    )
    assert resolved_history.status_code == 200
    assert resolved_history.json()["total"] == 1
    assert (
        resolved_history.json()["items"][0]["help_request_id"]
        == request_id
    )


def test_caregiver_can_resolve_pending_request_directly(
    help_request_app,
    db_session,
    patient,
):
    created, household, patient_user = create_patient_help_request(
        help_request_app,
        db_session,
        patient,
        message="Immediate help was provided",
    )
    _household, assigned, _unassigned = setup_caregivers(
        db_session,
        patient,
    )

    resolved = request(
        help_request_app,
        "POST",
        (
            f"/api/v1/help-requests/"
            f"{created['help_request_id']}/resolve"
        ),
        user=assigned,
        household_id=household.household_id,
    )

    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"
    assert resolved.json()["acknowledged_at"] is None
    assert resolved.json()["resolved_at"] is not None

    active = request(
        help_request_app,
        "GET",
        "/api/v1/help-requests/active",
        user=patient_user,
        household_id=household.household_id,
    )
    assert active.status_code == 200
    assert active.json() is None
def test_help_request_push_intents_commit_once(
    help_request_app,
    db_session,
    patient,
    monkeypatch,
):
    delivered = Mock()
    monkeypatch.setattr(
        "app.help_requests.notification_service.deliver_help_request_notifications",
        delivered,
    )

    household, patient_user, _owner = patient_identity(
        db_session,
        patient,
    )
    action_id = uuid4()
    request_body = payload(action_id, "Please call me")

    created = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=request_body,
    )
    assert created.status_code == 201

    request_id = UUID(created.json()["help_request_id"])
    delivered.assert_called_once()
    assert delivered.call_args.args[1] == {
        (request_id, "CREATED"),
    }

    delivered.reset_mock()
    replay = request(
        help_request_app,
        "POST",
        "/api/v1/help-requests",
        user=patient_user,
        household_id=household.household_id,
        json=request_body,
    )
    assert replay.status_code == 201
    assert replay.json()["idempotent"] is True
    delivered.assert_not_called()

    _household, assigned, _unassigned = setup_caregivers(
        db_session,
        patient,
    )
    delivered.reset_mock()

    acknowledged = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/acknowledge",
        user=assigned,
        household_id=household.household_id,
    )
    assert acknowledged.status_code == 200
    delivered.assert_called_once()
    assert delivered.call_args.args[1] == {
        (request_id, "ACKNOWLEDGED"),
    }

    delivered.reset_mock()
    acknowledged_replay = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/acknowledge",
        user=assigned,
        household_id=household.household_id,
    )
    assert acknowledged_replay.status_code == 200
    assert acknowledged_replay.json()["idempotent"] is True
    delivered.assert_not_called()

    resolved = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/resolve",
        user=assigned,
        household_id=household.household_id,
    )
    assert resolved.status_code == 200
    delivered.assert_called_once()
    assert delivered.call_args.args[1] == {
        (request_id, "RESOLVED"),
    }

    delivered.reset_mock()
    resolved_replay = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/resolve",
        user=assigned,
        household_id=household.household_id,
    )
    assert resolved_replay.status_code == 200
    assert resolved_replay.json()["idempotent"] is True
    delivered.assert_not_called()


def test_help_request_rollback_discards_push_intent(
    db_session,
    patient,
    monkeypatch,
):
    from app.help_requests.service import create_help_request

    delivered = Mock()
    monkeypatch.setattr(
        "app.help_requests.notification_service.deliver_help_request_notifications",
        delivered,
    )

    created, idempotent = create_help_request(
        db_session,
        patient=patient,
        client_action_id=uuid4(),
        message="Do not deliver before commit",
    )
    created_id = created.help_request_id

    assert idempotent is False
    delivered.assert_not_called()

    db_session.rollback()

    delivered.assert_not_called()
    assert db_session.get(HelpRequest, created_id) is None

def test_caregiver_adds_and_lists_help_request_notes(
    help_request_app,
    db_session,
    patient,
):
    created, household, _patient_user = create_patient_help_request(
        help_request_app,
        db_session,
        patient,
    )
    _household, assigned, _unassigned = setup_caregivers(
        db_session,
        patient,
    )
    request_id = created["help_request_id"]
    action_id = uuid4()
    first_payload = {
        "client_action_id": str(action_id),
        "note": "  Called Nana. Her daughter is on the way.  ",
    }

    first = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/notes",
        user=assigned,
        household_id=household.household_id,
        json=first_payload,
    )

    assert first.status_code == 201
    first_body = first.json()
    assert first_body["help_request_id"] == request_id
    assert first_body["author_user_id"] == str(assigned.user_id)
    assert first_body["author_display_name"] == assigned.full_name
    assert first_body["client_action_id"] == str(action_id)
    assert first_body["note"] == "Called Nana. Her daughter is on the way."
    assert first_body["idempotent"] is False

    replay = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/notes",
        user=assigned,
        household_id=household.household_id,
        json=first_payload,
    )

    assert replay.status_code == 201
    assert replay.json()["help_request_note_id"] == first_body["help_request_note_id"]
    assert replay.json()["idempotent"] is True

    changed_replay = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/notes",
        user=assigned,
        household_id=household.household_id,
        json={
            "client_action_id": str(action_id),
            "note": "Changed meaning",
        },
    )
    assert changed_replay.status_code == 409

    resolved = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/resolve",
        user=assigned,
        household_id=household.household_id,
    )
    assert resolved.status_code == 200

    second = request(
        help_request_app,
        "POST",
        f"/api/v1/help-requests/{request_id}/notes",
        user=assigned,
        household_id=household.household_id,
        json={
            "client_action_id": str(uuid4()),
            "note": "Follow-up completed after resolution.",
        },
    )
    assert second.status_code == 201

    listed = request(
        help_request_app,
        "GET",
        f"/api/v1/help-requests/{request_id}/notes",
        user=assigned,
        household_id=household.household_id,
    )

    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [item["note"] for item in body["items"]] == [
        "Called Nana. Her daughter is on the way.",
        "Follow-up completed after resolution.",
    ]

    paged = request(
        help_request_app,
        "GET",
        (f"/api/v1/help-requests/{request_id}/notes?limit=1&offset=1"),
        user=assigned,
        household_id=household.household_id,
    )
    assert paged.status_code == 200
    assert paged.json()["total"] == 2
    assert paged.json()["limit"] == 1
    assert paged.json()["offset"] == 1
    assert paged.json()["items"][0]["note"] == ("Follow-up completed after resolution.")

    stored = db_session.scalars(
        select(HelpRequestNote).where(
            HelpRequestNote.help_request_id == UUID(request_id)
        )
    ).all()
    assert len(stored) == 2


def test_help_request_notes_enforce_access_and_validation(
    help_request_app,
    db_session,
    patient,
):
    created, household, patient_user = create_patient_help_request(
        help_request_app,
        db_session,
        patient,
    )
    _household, assigned, unassigned = setup_caregivers(
        db_session,
        patient,
    )
    request_id = created["help_request_id"]
    path = f"/api/v1/help-requests/{request_id}/notes"

    patient_create = request(
        help_request_app,
        "POST",
        path,
        user=patient_user,
        household_id=household.household_id,
        json={
            "client_action_id": str(uuid4()),
            "note": "Patients cannot create private caregiver notes.",
        },
    )
    assert patient_create.status_code == 403

    patient_list = request(
        help_request_app,
        "GET",
        path,
        user=patient_user,
        household_id=household.household_id,
    )
    assert patient_list.status_code == 403

    unassigned_create = request(
        help_request_app,
        "POST",
        path,
        user=unassigned,
        household_id=household.household_id,
        json={
            "client_action_id": str(uuid4()),
            "note": "This request must remain undiscoverable.",
        },
    )
    assert unassigned_create.status_code == 404

    unassigned_list = request(
        help_request_app,
        "GET",
        path,
        user=unassigned,
        household_id=household.household_id,
    )
    assert unassigned_list.status_code == 404

    blank = request(
        help_request_app,
        "POST",
        path,
        user=assigned,
        household_id=household.household_id,
        json={
            "client_action_id": str(uuid4()),
            "note": "   ",
        },
    )
    assert blank.status_code == 422

    oversized = request(
        help_request_app,
        "POST",
        path,
        user=assigned,
        household_id=household.household_id,
        json={
            "client_action_id": str(uuid4()),
            "note": "x" * 1001,
        },
    )
    assert oversized.status_code == 422

    assert db_session.scalar(select(HelpRequestNote)) is None
