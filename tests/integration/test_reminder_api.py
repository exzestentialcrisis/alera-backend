import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.security import create_access_token
from app.core.config import Settings
from app.db.database import get_db
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.main import create_app
from app.patients.model import ElderlyPatient, Sex
from app.reminders.enums import (
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
)
from app.reminders.model import ReminderOccurrence, ReminderTemplate
from app.users.model import User, UserRole

pytestmark = pytest.mark.integration

JWT_SECRET = "reminder-api-test-secret-that-is-long-and-random-enough"
NOW = datetime(2026, 9, 11, 8, tzinfo=timezone.utc)


@pytest.fixture()
def reminder_api_app(db_session):
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


def add_reminder(db_session, patient, creator, *, scheduled_at=NOW, **overrides):
    template = ReminderTemplate(
        patient_id=patient.patient_id,
        created_by_user_id=creator.user_id,
        title=overrides.pop("title", "Morning Medication"),
        category=overrides.pop("category", ReminderCategory.MEDICATION),
        priority=overrides.pop("priority", ReminderPriority.NORMAL),
        start_date=scheduled_at.date(),
        start_time=scheduled_at.timetz().replace(tzinfo=None),
        timezone="Asia/Manila",
        **overrides,
    )
    db_session.add(template)
    db_session.flush()
    occurrence = ReminderOccurrence(
        reminder_template_id=template.reminder_template_id,
        scheduled_at=scheduled_at,
        due_at=scheduled_at + timedelta(minutes=30),
        status=ReminderOccurrenceStatus.UPCOMING,
    )
    db_session.add(occurrence)
    db_session.commit()
    return occurrence, template


def add_other_patient(db_session, *, owner=None):
    owner = owner or User(full_name="Other Admin", role=UserRole.CARE_ADMIN)
    patient_user = User(full_name="Other Patient", role=UserRole.ELDERLY_PATIENT)
    db_session.add_all([owner, patient_user])
    db_session.flush()
    household = Household(created_by_user_id=owner.user_id, household_name="Other Home")
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


def test_reminder_authentication_and_patient_access(reminder_api_app, db_session, patient):
    household = db_session.get(Household, patient.household_id)
    patient_user = db_session.get(User, patient.user_id)
    occurrence, _ = add_reminder(db_session, patient, household and db_session.get(User, household.created_by_user_id))
    _, _, other_household, other_patient = add_other_patient(db_session)

    assert request(reminder_api_app, "GET", "/api/v1/reminders").status_code == 401
    own_headers = headers(patient_user, household.household_id)
    listed = request(reminder_api_app, "GET", "/api/v1/reminders", headers=own_headers)
    assert listed.status_code == 200
    assert [item["reminder_occurrence_id"] for item in listed.json()["items"]] == [str(occurrence.reminder_occurrence_id)]
    assert request(reminder_api_app, "GET", f"/api/v1/reminders/{occurrence.reminder_occurrence_id}", headers=own_headers).status_code == 200
    assert request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={other_patient.patient_id}", headers=own_headers).status_code == 403

    other_occurrence, _ = add_reminder(
        db_session, other_patient, db_session.get(User, other_household.created_by_user_id)
    )
    inaccessible = request(reminder_api_app, "GET", f"/api/v1/reminders/{other_occurrence.reminder_occurrence_id}", headers=own_headers)
    nonexistent = request(reminder_api_app, "GET", f"/api/v1/reminders/{uuid4()}", headers=own_headers)
    assert inaccessible.status_code == nonexistent.status_code == 404
    assert inaccessible.json() == nonexistent.json()


def test_reminder_caregiver_and_admin_scope(reminder_api_app, db_session, patient):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    occurrence, _ = add_reminder(db_session, patient, owner)
    caregiver = User(full_name="Assigned Caregiver", role=UserRole.CAREGIVER)
    unassigned = User(full_name="Unassigned Caregiver", role=UserRole.CAREGIVER)
    db_session.add_all([caregiver, unassigned])
    db_session.flush()
    assignment = CaregiverPatientAssignment(
        caregiver_user_id=caregiver.user_id,
        patient_id=patient.patient_id,
        assigned_by_user_id=owner.user_id,
    )
    db_session.add(assignment)
    db_session.commit()
    caregiver_headers = headers(caregiver, household.household_id)
    assert request(reminder_api_app, "GET", "/api/v1/reminders", headers=caregiver_headers).status_code == 422
    assert request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={patient.patient_id}", headers=caregiver_headers).status_code == 200
    assert request(reminder_api_app, "GET", f"/api/v1/reminders/{occurrence.reminder_occurrence_id}", headers=caregiver_headers).status_code == 200
    denied_headers = headers(unassigned, household.household_id)
    assert request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={patient.patient_id}", headers=denied_headers).status_code == 404
    assignment.unassigned_at = NOW
    db_session.commit()
    assert request(reminder_api_app, "GET", f"/api/v1/reminders/{occurrence.reminder_occurrence_id}", headers=caregiver_headers).status_code == 404

    assert request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={patient.patient_id}", headers=headers(owner, household.household_id)).status_code == 200
    other_admin, _, other_household, other_patient = add_other_patient(db_session)
    assert request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={other_patient.patient_id}", headers=headers(owner, household.household_id)).status_code == 404
    assert request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={patient.patient_id}", headers=headers(other_admin, other_household.household_id)).status_code == 404


def test_reminder_filters_boundaries_pagination_and_joined_output(reminder_api_app, db_session, patient):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    first, first_template = add_reminder(db_session, patient, owner, scheduled_at=NOW, instructions="Take after breakfast.")
    second, _ = add_reminder(db_session, patient, owner, scheduled_at=NOW + timedelta(hours=1), category=ReminderCategory.HYDRATION)
    second.status = ReminderOccurrenceStatus.DUE
    db_session.commit()
    admin_headers = headers(owner, household.household_id)
    path = f"/api/v1/reminders?patient_id={patient.patient_id}&from_at=2026-09-11T08:00:00Z&before_at=2026-09-11T09:00:00Z&status=UPCOMING&status=DUE&category=MEDICATION"
    response = request(reminder_api_app, "GET", path, headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["reminder_occurrence_id"] == str(first.reminder_occurrence_id)
    assert body["items"][0]["title"] == first_template.title
    assert body["items"][0]["instructions"] == "Take after breakfast."
    category_filtered = request(
        reminder_api_app,
        "GET",
        f"/api/v1/reminders?patient_id={patient.patient_id}"
        "&category=MEDICATION&category=HYDRATION",
        headers=admin_headers,
    ).json()
    assert category_filtered["total"] == 2
    assert request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={patient.patient_id}&from_at=2026-09-11T08:00:00", headers=admin_headers).status_code == 422
    assert request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={patient.patient_id}&from_at=2026-09-11T09:00:00Z&before_at=2026-09-11T08:00:00Z", headers=admin_headers).status_code == 422
    paged = request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={patient.patient_id}&limit=1&offset=1", headers=admin_headers).json()
    assert paged["total"] == 2
    assert paged["items"][0]["reminder_occurrence_id"] == str(second.reminder_occurrence_id)
    empty = request(reminder_api_app, "GET", f"/api/v1/reminders?patient_id={patient.patient_id}&category=MEAL", headers=admin_headers).json()
    assert empty == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_reminders_with_the_same_time_are_ordered_by_occurrence_id(
    reminder_api_app, db_session, patient
):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    first, _ = add_reminder(db_session, patient, owner, scheduled_at=NOW)
    second, _ = add_reminder(db_session, patient, owner, scheduled_at=NOW)

    response = request(
        reminder_api_app,
        "GET",
        f"/api/v1/reminders?patient_id={patient.patient_id}",
        headers=headers(owner, household.household_id),
    )

    assert response.status_code == 200
    assert [item["reminder_occurrence_id"] for item in response.json()["items"]] == [
        str(item) for item in sorted((first.reminder_occurrence_id, second.reminder_occurrence_id))
    ]
