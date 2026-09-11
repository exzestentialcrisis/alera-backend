import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.security import create_access_token
from app.core.config import Settings
from app.core.time import utc_now
from app.db.database import get_db
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
JWT_SECRET = "reminder-patient-actions-test-secret-that-is-long-enough"
NOW = datetime(2026, 9, 11, 8, tzinfo=timezone.utc)


@pytest.fixture()
def patient_action_app(db_session):
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


def auth_headers(user, household_id):
    token, _ = create_access_token(
        user_id=user.user_id,
        household_id=household_id,
        secret=JWT_SECRET,
        expires_minutes=30,
    )
    return {"Authorization": f"Bearer {token}"}


def add_reminder(db_session, patient, creator, **template_overrides):
    template = ReminderTemplate(
        patient_id=patient.patient_id,
        created_by_user_id=creator.user_id,
        title="Morning medication",
        category=ReminderCategory.MEDICATION,
        priority=ReminderPriority.NORMAL,
        start_date=NOW.date(),
        start_time=NOW.time(),
        timezone="Asia/Manila",
        **template_overrides,
    )
    db_session.add(template)
    db_session.flush()
    occurrence = ReminderOccurrence(
        reminder_template_id=template.reminder_template_id,
        scheduled_at=NOW,
        due_at=NOW + timedelta(minutes=30),
        status=ReminderOccurrenceStatus.UPCOMING,
    )
    db_session.add(occurrence)
    db_session.commit()
    return occurrence, template


def add_other_patient(db_session):
    owner = User(full_name="Other owner", role=UserRole.CARE_ADMIN)
    patient_user = User(full_name="Other patient", role=UserRole.ELDERLY_PATIENT)
    db_session.add_all([owner, patient_user])
    db_session.flush()
    household = Household(created_by_user_id=owner.user_id, household_name="Other")
    db_session.add(household)
    db_session.flush()
    patient = ElderlyPatient(
        user_id=patient_user.user_id,
        household_id=household.household_id,
        birthdate=datetime(1950, 1, 1).date(),
        sex=Sex.OTHER,
    )
    db_session.add(patient)
    db_session.commit()
    return owner, patient_user, household, patient


def patient_context(db_session, patient):
    household = db_session.get(Household, patient.household_id)
    return db_session.get(User, patient.user_id), household, db_session.get(
        User, household.created_by_user_id
    )


def test_complete_transitions_audits_and_normalizes_note(patient_action_app, db_session, patient):
    patient_user, household, owner = patient_context(db_session, patient)
    occurrence, _ = add_reminder(db_session, patient, owner)
    occurrence.due_at = utc_now() + timedelta(days=1)
    db_session.commit()
    due_at, scheduled_at = occurrence.due_at, occurrence.scheduled_at
    response = request(
        patient_action_app,
        "POST",
        f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/complete",
        headers=auth_headers(patient_user, household.household_id),
        json={"client_action_id": str(uuid4()), "note": "   "},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["idempotent"] is False
    assert body["reminder"]["status"] == "COMPLETED"
    assert body["reminder"]["due_at"] == due_at.isoformat().replace("+00:00", "Z")
    assert body["reminder"]["scheduled_at"] == scheduled_at.isoformat().replace("+00:00", "Z")
    assert body["action"] | {"metadata": body["action"]["metadata"]} == {
        "reminder_action_id": body["action"]["reminder_action_id"],
        "client_action_id": body["action"]["client_action_id"],
        "reminder_occurrence_id": str(occurrence.reminder_occurrence_id),
        "performed_by_user_id": str(patient_user.user_id),
        "action_type": "MARK_COMPLETED",
        "action_note": None,
        "previous_status": "UPCOMING",
        "new_status": "COMPLETED",
        "new_due_at": None,
        "metadata": {},
        "performed_at": body["action"]["performed_at"],
    }


@pytest.mark.parametrize(
    ("starting_status", "late"),
    [
        (ReminderOccurrenceStatus.DUE, False),
        (ReminderOccurrenceStatus.SNOOZED, False),
        (ReminderOccurrenceStatus.MISSED, True),
    ],
)
def test_complete_accepts_due_snoozed_and_missed(
    patient_action_app, db_session, patient, starting_status, late
):
    patient_user, household, owner = patient_context(db_session, patient)
    occurrence, _ = add_reminder(db_session, patient, owner)
    occurrence.status = starting_status
    occurrence.due_at = NOW + timedelta(days=1) if not late else NOW - timedelta(days=1)
    db_session.commit()
    response = request(
        patient_action_app, "POST", f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/complete",
        headers=auth_headers(patient_user, household.household_id),
        json={"client_action_id": str(uuid4())},
    )
    assert response.status_code == 200
    assert response.json()["reminder"]["status"] == (
        "COMPLETED_LATE" if late else "COMPLETED"
    )


def test_snooze_idempotency_and_second_snooze(patient_action_app, db_session, patient):
    patient_user, household, owner = patient_context(db_session, patient)
    occurrence, template = add_reminder(db_session, patient, owner, default_snooze_minutes=7)
    original_due_at, scheduled_at = occurrence.due_at, occurrence.scheduled_at
    client_action_id = uuid4()
    payload = {"client_action_id": str(client_action_id), "note": "  later  "}
    path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/snooze"
    first = request(patient_action_app, "POST", path, headers=auth_headers(patient_user, household.household_id), json=payload)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["reminder"]["status"] == "SNOOZED"
    assert first_body["reminder"]["scheduled_at"] == scheduled_at.isoformat().replace("+00:00", "Z")
    assert first_body["action"]["metadata"] == {
        "snooze_minutes": 7,
        "previous_due_at": original_due_at.isoformat().replace("+00:00", "+00:00"),
    }
    replay = request(patient_action_app, "POST", path, headers=auth_headers(patient_user, household.household_id), json=payload)
    assert replay.status_code == 200
    assert replay.json()["idempotent"] is True
    assert replay.json()["action"]["reminder_action_id"] == first_body["action"]["reminder_action_id"]
    assert replay.json()["action"]["performed_at"] == first_body["action"]["performed_at"]
    assert replay.json()["reminder"]["due_at"] == first_body["reminder"]["due_at"]
    second = request(
        patient_action_app, "POST", path,
        headers=auth_headers(patient_user, household.household_id),
        json={"client_action_id": str(uuid4()), "snooze_minutes": 12},
    )
    assert second.status_code == 200
    assert second.json()["action"]["metadata"]["snooze_minutes"] == 12
    assert db_session.scalars(select(ReminderAction).where(
        ReminderAction.reminder_occurrence_id == occurrence.reminder_occurrence_id
    )).all().__len__() == 2


def test_patient_action_conflicts_validation_and_scope(patient_action_app, db_session, patient):
    patient_user, household, owner = patient_context(db_session, patient)
    occurrence, template = add_reminder(db_session, patient, owner)
    complete_path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/complete"
    action_id = uuid4()
    assert request(patient_action_app, "POST", complete_path, headers=auth_headers(owner, household.household_id), json={"client_action_id": str(action_id)}).status_code == 403
    assert request(patient_action_app, "POST", complete_path, headers=auth_headers(patient_user, household.household_id), json={"client_action_id": str(action_id), "note": "x" * 1001}).status_code == 422
    occurrence.status = ReminderOccurrenceStatus.CANCELED
    db_session.commit()
    assert request(patient_action_app, "POST", complete_path, headers=auth_headers(patient_user, household.household_id), json={"client_action_id": str(action_id)}).status_code == 409
    other_owner, _, other_household, other_patient = add_other_patient(db_session)
    other_occurrence, _ = add_reminder(db_session, other_patient, other_owner)
    forbidden = request(patient_action_app, "POST", f"/api/v1/reminders/{other_occurrence.reminder_occurrence_id}/snooze", headers=auth_headers(patient_user, household.household_id), json={"client_action_id": str(uuid4())})
    missing = request(patient_action_app, "POST", f"/api/v1/reminders/{uuid4()}/snooze", headers=auth_headers(patient_user, household.household_id), json={"client_action_id": str(uuid4())})
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json() == missing.json()
    occurrence.status = ReminderOccurrenceStatus.UPCOMING
    template.snooze_allowed = False
    db_session.commit()
    snooze_path = f"/api/v1/reminders/{occurrence.reminder_occurrence_id}/snooze"
    assert request(patient_action_app, "POST", snooze_path, headers=auth_headers(patient_user, household.household_id), json={"client_action_id": str(uuid4())}).status_code == 409
    assert request(patient_action_app, "POST", snooze_path, headers=auth_headers(patient_user, household.household_id), json={"client_action_id": str(uuid4()), "snooze_minutes": 0}).status_code == 422
