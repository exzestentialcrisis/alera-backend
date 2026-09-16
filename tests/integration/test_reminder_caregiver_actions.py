import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.security import create_access_token
from app.core.config import Settings
from app.core.time import utc_now
from app.db.database import get_db
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.main import create_app
from app.patients.model import ElderlyPatient, Sex
from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
)
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate
from app.users.model import User, UserRole


pytestmark = pytest.mark.integration
JWT_SECRET = "reminder-care-actions-test-secret-that-is-long-enough"
NOW = datetime(2026, 9, 11, 8, tzinfo=timezone.utc)


@pytest.fixture()
def caregiver_action_app(db_session):
    app = create_app(
        Settings(environment="testing", database_url=None, alera_jwt_secret=JWT_SECRET)
    )

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return app


def request(app, method, path, **kwargs):
    async def send():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def headers(user, household_id):
    token, _ = create_access_token(
        user_id=user.user_id, household_id=household_id,
        secret=JWT_SECRET, expires_minutes=30,
    )
    return {"Authorization": f"Bearer {token}"}


def setup_reminder(db_session, patient):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    caregiver = User(full_name="Assigned caregiver", role=UserRole.CAREGIVER)
    unassigned = User(full_name="Unassigned caregiver", role=UserRole.CAREGIVER)
    db_session.add_all([caregiver, unassigned])
    db_session.flush()
    db_session.add(CaregiverPatientAssignment(
        caregiver_user_id=caregiver.user_id,
        patient_id=patient.patient_id,
        assigned_by_user_id=owner.user_id,
    ))
    template = ReminderTemplate(
        patient_id=patient.patient_id, created_by_user_id=owner.user_id,
        title="Care reminder", category=ReminderCategory.MEDICATION,
        priority=ReminderPriority.NORMAL, start_date=NOW.date(), start_time=NOW.time(),
        timezone="Asia/Manila",
    )
    db_session.add(template)
    db_session.flush()
    occurrence = ReminderOccurrence(
        reminder_template_id=template.reminder_template_id, scheduled_at=NOW,
        due_at=NOW + timedelta(minutes=30), status=ReminderOccurrenceStatus.UPCOMING,
    )
    db_session.add(occurrence)
    db_session.commit()
    return household, owner, caregiver, unassigned, occurrence


def test_history_scopes_orders_and_paginates(caregiver_action_app, db_session, patient):
    household, owner, caregiver, unassigned, occurrence = setup_reminder(db_session, patient)
    patient_user = db_session.get(User, patient.user_id)
    earlier, later = uuid4(), uuid4()
    db_session.add_all([
        ReminderAction(
            reminder_occurrence_id=occurrence.reminder_occurrence_id,
            performed_by_user_id=caregiver.user_id,
            action_type=ReminderActionType.ADD_NOTE, action_note="legacy",
            previous_status=occurrence.status, new_status=occurrence.status,
            action_metadata={}, performed_at=NOW,
        ),
        ReminderAction(
            client_action_id=uuid4(), reminder_occurrence_id=occurrence.reminder_occurrence_id,
            performed_by_user_id=owner.user_id,
            action_type=ReminderActionType.FOLLOW_UP, action_note="later",
            previous_status=occurrence.status, new_status=occurrence.status,
            action_metadata={}, performed_at=NOW + timedelta(minutes=1),
        ),
    ])
    db_session.commit()
    path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/actions"
    for user in (patient_user, caregiver, owner):
        response = request(caregiver_action_app, "GET", path, headers=headers(user, household.household_id))
        assert response.status_code == 200
        assert response.json()["total"] == 2
        assert response.json()["items"][0]["client_action_id"] is None
    paged = request(caregiver_action_app, "GET", f"{path}?limit=1&offset=1", headers=headers(caregiver, household.household_id))
    assert paged.json()["total"] == 2 and len(paged.json()["items"]) == 1
    denied = request(caregiver_action_app, "GET", path, headers=headers(unassigned, household.household_id))
    missing = request(caregiver_action_app, "GET", f"/api/v1/reminders/{uuid4()}/actions", headers=headers(unassigned, household.household_id))
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()


def test_notes_follow_ups_and_idempotency(caregiver_action_app, db_session, patient):
    household, owner, caregiver, unassigned, occurrence = setup_reminder(db_session, patient)
    patient_user = db_session.get(User, patient.user_id)
    before = (occurrence.status, occurrence.due_at, occurrence.scheduled_at)
    action_id = uuid4()
    path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/notes"
    payload = {"client_action_id": str(action_id), "note": "  Called patient.  "}
    first = request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json=payload)
    assert first.status_code == 200 and first.json()["action"]["action_note"] == "Called patient."
    replay = request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json=payload)
    assert replay.status_code == 200 and replay.json()["idempotent"] is True
    assert replay.json()["action"]["reminder_action_id"] == first.json()["action"]["reminder_action_id"]
    assert request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json={"client_action_id": str(action_id), "note": "different"}).status_code == 409
    assert request(caregiver_action_app, "POST", path, headers=headers(patient_user, household.household_id), json={"client_action_id": str(uuid4()), "note": "no"}).status_code == 403
    assert request(caregiver_action_app, "POST", path, headers=headers(unassigned, household.household_id), json={"client_action_id": str(uuid4()), "note": "no"}).status_code == 404
    assert request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json={"client_action_id": str(uuid4()), "note": "   "}).status_code == 422
    occurrence.status = ReminderOccurrenceStatus.CANCELED
    db_session.commit()
    follow_up = request(caregiver_action_app, "POST", f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/follow-ups", headers=headers(owner, household.household_id), json={"client_action_id": str(uuid4()), "note": "Documented follow-up"})
    assert follow_up.status_code == 200 and follow_up.json()["action"]["action_type"] == "FOLLOW_UP"
    db_session.refresh(occurrence)
    assert (occurrence.status, occurrence.due_at, occurrence.scheduled_at) == (
        ReminderOccurrenceStatus.CANCELED, before[1], before[2]
    )


def test_mark_missed_handled_is_single_and_idempotent(caregiver_action_app, db_session, patient):
    household, owner, caregiver, unassigned, occurrence = setup_reminder(db_session, patient)
    occurrence.status = ReminderOccurrenceStatus.MISSED
    db_session.commit()
    path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/missed/handle"
    action_id = uuid4()
    payload = {"client_action_id": str(action_id), "note": "  called family  "}
    first = request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json=payload)
    assert first.status_code == 200
    assert first.json()["action"]["action_type"] == "MARK_MISSED_HANDLED"
    assert first.json()["action"]["previous_status"] == first.json()["action"]["new_status"] == "MISSED"
    replay = request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json=payload)
    assert replay.status_code == 200 and replay.json()["idempotent"] is True
    assert request(caregiver_action_app, "POST", path, headers=headers(owner, household.household_id), json={"client_action_id": str(uuid4())}).status_code == 409
    db_session.refresh(occurrence)
    assert occurrence.status is ReminderOccurrenceStatus.MISSED
    occurrence.status = ReminderOccurrenceStatus.COMPLETED
    db_session.commit()
    assert request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json={"client_action_id": str(uuid4())}).status_code == 409


@pytest.mark.parametrize(
    ("starting_status", "late", "expected_status"),
    [
        (ReminderOccurrenceStatus.UPCOMING, False, "COMPLETED"),
        (ReminderOccurrenceStatus.DUE, False, "COMPLETED"),
        (ReminderOccurrenceStatus.SNOOZED, False, "COMPLETED"),
        (ReminderOccurrenceStatus.MISSED, False, "COMPLETED_LATE"),
    ],
)
def test_complete_on_behalf_transitions_audits_and_replays(
    caregiver_action_app, db_session, patient, starting_status, late, expected_status
):
    household, owner, caregiver, _unassigned, occurrence = setup_reminder(
        db_session, patient
    )
    occurrence.status = starting_status
    occurrence.due_at = utc_now() - timedelta(minutes=1) if late else utc_now() + timedelta(days=1)
    original_times = occurrence.scheduled_at, occurrence.due_at
    db_session.commit()
    path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/complete-on-behalf"
    action_id = uuid4()
    payload = {"client_action_id": str(action_id), "note": "  Confirmed by carer.  "}

    response = request(
        caregiver_action_app, "POST", path,
        headers=headers(caregiver, household.household_id), json=payload,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reminder"]["status"] == expected_status
    assert body["reminder"]["scheduled_at"] == original_times[0].isoformat().replace("+00:00", "Z")
    assert body["reminder"]["due_at"] == original_times[1].isoformat().replace("+00:00", "Z")
    assert body["action"] | {"metadata": body["action"]["metadata"]} == {
        "reminder_action_id": body["action"]["reminder_action_id"],
        "client_action_id": str(action_id),
        "reminder_occurrence_id": str(occurrence.reminder_occurrence_id),
        "performed_by_user_id": str(caregiver.user_id),
        "action_type": "CAREGIVER_OVERRIDE",
        "action_note": "Confirmed by carer.",
        "previous_status": starting_status.value,
        "new_status": expected_status,
        "new_due_at": None,
        "metadata": {"operation": "COMPLETE_ON_BEHALF"},
        "performed_at": body["action"]["performed_at"],
    }
    replay = request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json=payload)
    assert replay.status_code == 200 and replay.json()["idempotent"] is True
    assert replay.json()["action"]["reminder_action_id"] == body["action"]["reminder_action_id"]
    assert request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json={"client_action_id": str(action_id), "note": "changed"}).status_code == 409


@pytest.mark.parametrize(
    "starting_status", [ReminderOccurrenceStatus.UPCOMING, ReminderOccurrenceStatus.DUE, ReminderOccurrenceStatus.SNOOZED]
)
def test_cancel_transitions_audits_and_replays(
    caregiver_action_app, db_session, patient, starting_status
):
    household, owner, caregiver, _unassigned, occurrence = setup_reminder(
        db_session, patient
    )
    occurrence.status = starting_status
    before = occurrence.scheduled_at, occurrence.due_at
    db_session.commit()
    path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/cancel"
    action_id = uuid4()
    payload = {"client_action_id": str(action_id), "note": "  No longer needed.  "}
    response = request(caregiver_action_app, "POST", path, headers=headers(owner, household.household_id), json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["reminder"]["status"] == "CANCELED"
    assert body["reminder"]["scheduled_at"] == before[0].isoformat().replace("+00:00", "Z")
    assert body["reminder"]["due_at"] == before[1].isoformat().replace("+00:00", "Z")
    assert body["action"]["action_type"] == "CANCEL"
    assert body["action"]["action_note"] == "No longer needed."
    assert body["action"]["previous_status"] == starting_status.value
    assert body["action"]["new_status"] == "CANCELED"
    assert body["action"]["new_due_at"] is None and body["action"]["metadata"] == {}
    replay = request(caregiver_action_app, "POST", path, headers=headers(owner, household.household_id), json=payload)
    assert replay.status_code == 200 and replay.json()["idempotent"] is True


@pytest.mark.parametrize("endpoint", ["complete-on-behalf", "cancel"])
def test_caregiver_mutations_enforce_scope_validation_and_terminal_states(
    caregiver_action_app, db_session, patient, endpoint
):
    household, owner, caregiver, unassigned, occurrence = setup_reminder(db_session, patient)
    patient_user = db_session.get(User, patient.user_id)
    path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/{endpoint}"
    payload = {"client_action_id": str(uuid4()), "note": "required reason"}
    assert request(caregiver_action_app, "POST", path, headers=headers(patient_user, household.household_id), json=payload).status_code == 403
    assert request(caregiver_action_app, "POST", path, headers=headers(unassigned, household.household_id), json={**payload, "client_action_id": str(uuid4())}).status_code == 404
    for invalid_payload in (
        {"note": "reason"},
        {"client_action_id": str(uuid4()), "note": "  "},
        {"client_action_id": str(uuid4()), "note": "x" * 1001},
        {"client_action_id": str(uuid4()), "note": "reason", "extra": True},
    ):
        assert request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json=invalid_payload).status_code == 422
    occurrence.status = ReminderOccurrenceStatus.MISSED if endpoint == "cancel" else ReminderOccurrenceStatus.CANCELED
    db_session.commit()
    assert request(caregiver_action_app, "POST", path, headers=headers(caregiver, household.household_id), json={**payload, "client_action_id": str(uuid4())}).status_code == 409
