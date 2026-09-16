import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth.security import create_access_token
from app.core.config import Settings
from app.db.database import get_db
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.main import create_app
from app.reminders.enums import ReminderActionType, ReminderOccurrenceStatus, ReminderTemplateStatus
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate
from app.users.model import User, UserRole


pytestmark = pytest.mark.integration
JWT_SECRET = "reminder-template-test-secret-that-is-long-and-random-enough"


@pytest.fixture()
def reminder_template_app(db_session):
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
        user_id=user.user_id,
        household_id=household_id,
        secret=JWT_SECRET,
        expires_minutes=30,
    )
    return {"Authorization": f"Bearer {token}"}


def setup_caregivers(db_session, patient):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    patient_user = db_session.get(User, patient.user_id)
    caregiver = User(full_name="Assigned Caregiver", role=UserRole.CAREGIVER)
    unassigned = User(full_name="Unassigned Caregiver", role=UserRole.CAREGIVER)
    db_session.add_all([caregiver, unassigned])
    db_session.flush()
    db_session.add(
        CaregiverPatientAssignment(
            caregiver_user_id=caregiver.user_id,
            patient_id=patient.patient_id,
            assigned_by_user_id=owner.user_id,
        )
    )
    db_session.commit()
    return household, owner, patient_user, caregiver, unassigned


def create_payload(patient_id, **overrides):
    payload = {
        "patient_id": str(patient_id),
        "title": "  Morning Medication  ",
        "category": "MEDICATION",
        "instructions": "  Take after breakfast.  ",
        "priority": "NORMAL",
        "start_date": "2026-09-17",
        "start_time": "08:00:00",
        "timezone": "Asia/Manila",
        "due_after_minutes": 15,
        "snooze_allowed": True,
        "default_snooze_minutes": 10,
        "missed_after_minutes": 30,
        "notification_channels": "IN_APP",
    }
    payload.update(overrides)
    return payload


def test_assigned_caregiver_creates_normalized_template(
    reminder_template_app, db_session, patient
):
    household, _owner, _patient_user, caregiver, _unassigned = setup_caregivers(
        db_session, patient
    )

    response = request(
        reminder_template_app,
        "POST",
        "/api/v1/reminder-templates",
        headers=headers(caregiver, household.household_id),
        json=create_payload(patient.patient_id),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["patient_id"] == str(patient.patient_id)
    assert body["created_by_user_id"] == str(caregiver.user_id)
    assert body["title"] == "Morning Medication"
    assert body["instructions"] == "Take after breakfast."
    assert body["status"] == "ACTIVE"
    assert body["schedule_rule"] is None
    assert db_session.get(
        ReminderTemplate, UUID(body["reminder_template_id"])
    ) is not None


def test_template_saves_materialize_and_reconcile_future_occurrences(
    reminder_template_app, db_session, patient, monkeypatch
):
    """Template changes replace future schedules but retain canceled history."""
    fixed_now = datetime(2026, 9, 16, tzinfo=timezone.utc)
    monkeypatch.setattr("app.reminders.template_service.utc_now", lambda: fixed_now)
    household, _owner, _patient_user, caregiver, _unassigned = setup_caregivers(
        db_session, patient
    )
    auth = headers(caregiver, household.household_id)
    created = request(
        reminder_template_app,
        "POST",
        "/api/v1/reminder-templates",
        headers=auth,
        json=create_payload(
            patient.patient_id,
            start_date="2026-09-16",
            start_time="08:00:00",
            schedule_rule="FREQ=DAILY",
        ),
    )
    assert created.status_code == 201
    template_id = UUID(created.json()["reminder_template_id"])
    assert created.json()["schedule_rule"] == "FREQ=DAILY"
    assert db_session.scalar(
        select(func.count(ReminderOccurrence.reminder_occurrence_id)).where(
            ReminderOccurrence.reminder_template_id == template_id,
            ReminderOccurrence.status == ReminderOccurrenceStatus.UPCOMING,
        )
    ) == 60

    changed = request(
        reminder_template_app,
        "PATCH",
        f"/api/v1/reminder-templates/{template_id}",
        headers=auth,
        json={"start_time": "09:00:00", "schedule_rule": "FREQ=WEEKLY;BYDAY=MO,WE,FR"},
    )
    assert changed.status_code == 200
    assert changed.json()["schedule_rule"] == "FREQ=WEEKLY;BYDAY=MO,WE,FR"
    assert db_session.scalar(
        select(func.count(ReminderOccurrence.reminder_occurrence_id)).where(
            ReminderOccurrence.reminder_template_id == template_id,
            ReminderOccurrence.status == ReminderOccurrenceStatus.CANCELED,
        )
    ) == 60
    assert db_session.scalar(
        select(func.count(ReminderOccurrence.reminder_occurrence_id)).where(
            ReminderOccurrence.reminder_template_id == template_id,
            ReminderOccurrence.status == ReminderOccurrenceStatus.UPCOMING,
        )
    ) == 26

    disabled = request(
        reminder_template_app,
        "PATCH",
        f"/api/v1/reminder-templates/{template_id}",
        headers=auth,
        json={"status": "DISABLED"},
    )
    assert disabled.status_code == 200
    assert db_session.scalar(
        select(func.count(ReminderOccurrence.reminder_occurrence_id)).where(
            ReminderOccurrence.reminder_template_id == template_id,
            ReminderOccurrence.status == ReminderOccurrenceStatus.UPCOMING,
        )
    ) == 0

    enabled = request(
        reminder_template_app,
        "PATCH",
        f"/api/v1/reminder-templates/{template_id}",
        headers=auth,
        json={"status": "ACTIVE"},
    )
    assert enabled.status_code == 200
    assert db_session.scalar(
        select(func.count(ReminderOccurrence.reminder_occurrence_id)).where(
            ReminderOccurrence.reminder_template_id == template_id,
            ReminderOccurrence.status == ReminderOccurrenceStatus.UPCOMING,
        )
    ) == 26
    assert db_session.scalar(
        select(func.count(ReminderAction.reminder_action_id)).where(
            ReminderAction.action_type == ReminderActionType.CANCEL,
            ReminderAction.action_metadata["source"].astext == "template_reconciliation",
        )
    ) == 86

    archived = request(
        reminder_template_app,
        "POST",
        f"/api/v1/reminder-templates/{template_id}/archive",
        headers=auth,
    )
    assert archived.status_code == 200
    assert db_session.scalar(
        select(func.count(ReminderOccurrence.reminder_occurrence_id)).where(
            ReminderOccurrence.reminder_template_id == template_id,
            ReminderOccurrence.status == ReminderOccurrenceStatus.UPCOMING,
        )
    ) == 0


@pytest.mark.parametrize("actor_name", ["owner", "patient_user"])
def test_patient_and_admin_cannot_create_templates(
    reminder_template_app, db_session, patient, actor_name
):
    household, owner, patient_user, _caregiver, _unassigned = setup_caregivers(
        db_session, patient
    )
    actor = {"owner": owner, "patient_user": patient_user}[actor_name]

    response = request(
        reminder_template_app,
        "POST",
        "/api/v1/reminder-templates",
        headers=headers(actor, household.household_id),
        json=create_payload(patient.patient_id),
    )

    assert response.status_code == 403


def test_unassigned_and_removed_caregivers_receive_scoped_not_found(
    reminder_template_app, db_session, patient
):
    household, _owner, _patient_user, caregiver, unassigned = setup_caregivers(
        db_session, patient
    )
    path = "/api/v1/reminder-templates"
    payload = create_payload(patient.patient_id)

    missing = request(
        reminder_template_app,
        "POST",
        path,
        headers=headers(unassigned, household.household_id),
        json=payload,
    )
    assert missing.status_code == 404

    assignment = db_session.query(CaregiverPatientAssignment).filter_by(
        caregiver_user_id=caregiver.user_id,
        patient_id=patient.patient_id,
    ).one()
    from app.core.time import utc_now

    assignment.unassigned_at = utc_now()
    db_session.commit()
    removed = request(
        reminder_template_app,
        "POST",
        path,
        headers=headers(caregiver, household.household_id),
        json=payload,
    )
    assert removed.status_code == 404
    assert removed.json() == missing.json()


def test_template_list_detail_filters_and_pagination(
    reminder_template_app, db_session, patient
):
    household, _owner, _patient_user, caregiver, _unassigned = setup_caregivers(
        db_session, patient
    )
    auth = headers(caregiver, household.household_id)
    ids = []
    for title, start_time in (("First", "08:00:00"), ("Second", "09:00:00")):
        response = request(
            reminder_template_app,
            "POST",
            "/api/v1/reminder-templates",
            headers=auth,
            json=create_payload(patient.patient_id, title=title, start_time=start_time),
        )
        assert response.status_code == 201
        ids.append(response.json()["reminder_template_id"])

    listed = request(
        reminder_template_app,
        "GET",
        f"/api/v1/reminder-templates?patient_id={patient.patient_id}&limit=1&offset=1",
        headers=auth,
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert [item["title"] for item in listed.json()["items"]] == ["Second"]

    detail = request(
        reminder_template_app,
        "GET",
        f"/api/v1/reminder-templates/{ids[0]}",
        headers=auth,
    )
    assert detail.status_code == 200
    assert detail.json()["title"] == "First"

    filtered = request(
        reminder_template_app,
        "GET",
        f"/api/v1/reminder-templates?patient_id={patient.patient_id}&status=DISABLED",
        headers=auth,
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 0


def test_update_archive_and_validation(reminder_template_app, db_session, patient):
    household, _owner, _patient_user, caregiver, _unassigned = setup_caregivers(
        db_session, patient
    )
    auth = headers(caregiver, household.household_id)
    created = request(
        reminder_template_app,
        "POST",
        "/api/v1/reminder-templates",
        headers=auth,
        json=create_payload(patient.patient_id),
    )
    template_id = created.json()["reminder_template_id"]
    path = f"/api/v1/reminder-templates/{template_id}"

    updated = request(
        reminder_template_app,
        "PATCH",
        path,
        headers=auth,
        json={"title": "  Evening Medication  ", "status": "DISABLED"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Evening Medication"
    assert updated.json()["status"] == "DISABLED"

    assert request(
        reminder_template_app, "PATCH", path, headers=auth, json={}
    ).status_code == 422
    assert request(
        reminder_template_app,
        "PATCH",
        path,
        headers=auth,
        json={"title": None},
    ).status_code == 422
    assert request(
        reminder_template_app,
        "PATCH",
        path,
        headers=auth,
        json={"snooze_allowed": True, "default_snooze_minutes": 0},
    ).status_code == 422
    assert request(
        reminder_template_app,
        "PATCH",
        path,
        headers=auth,
        json={"status": "ARCHIVED"},
    ).status_code == 422

    archived = request(
        reminder_template_app,
        "POST",
        f"{path}/archive",
        headers=auth,
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    assert archived.json()["archived_at"] is not None
    replay = request(
        reminder_template_app,
        "POST",
        f"{path}/archive",
        headers=auth,
    )
    assert replay.status_code == 200
    assert replay.json()["archived_at"] == archived.json()["archived_at"]
    assert request(
        reminder_template_app,
        "PATCH",
        path,
        headers=auth,
        json={"title": "Too late"},
    ).status_code == 409

    default_list = request(
        reminder_template_app,
        "GET",
        f"/api/v1/reminder-templates?patient_id={patient.patient_id}",
        headers=auth,
    )
    archived_list = request(
        reminder_template_app,
        "GET",
        f"/api/v1/reminder-templates?patient_id={patient.patient_id}&status=ARCHIVED",
        headers=auth,
    )
    assert default_list.json()["total"] == 0
    assert archived_list.json()["total"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("timezone", "Mars/Olympus_Mons"),
        ("start_time", "08:00:00+08:00"),
        ("default_snooze_minutes", 0),
        ("notification_channels", "EMAIL"),
    ],
)
def test_create_validation(
    reminder_template_app, db_session, patient, field, value
):
    household, _owner, _patient_user, caregiver, _unassigned = setup_caregivers(
        db_session, patient
    )
    response = request(
        reminder_template_app,
        "POST",
        "/api/v1/reminder-templates",
        headers=headers(caregiver, household.household_id),
        json=create_payload(patient.patient_id, **{field: value}),
    )
    assert response.status_code == 422
