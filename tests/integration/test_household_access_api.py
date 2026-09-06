import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import Settings
from app.auth.security import create_access_token
from uuid import uuid4
from app.db.database import get_db
from app.household_access.model import (
    CaregiverPatientAssignment,
    PatientAccessCode,
)
from app.household_access.security import verify_access_code
from app.households.model import Household, HouseholdStatus
from app.main import create_app
from app.patients.model import ElderlyPatient, Sex
from app.users.model import AccountStatus, User, UserRole

pytestmark = pytest.mark.integration


@pytest.fixture()
def api_app(db_session):
    app = create_app(Settings(environment="testing", database_url=None, alera_jwt_secret="test-secret"))

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


def headers(user, *, bearer=False):
    token, _ = create_access_token(user_id=user.user_id, household_id=getattr(user, "test_household_id", uuid4()), secret="test-secret", expires_minutes=30)
    return {"Authorization": f"Bearer {token}"} if bearer else {"X-Alera-Actor-Id": str(user.user_id)}


def build_household(db, name):
    admin = User(full_name=f"{name} Admin", role=UserRole.CARE_ADMIN)
    patient_user = User(full_name=f"{name} Patient", role=UserRole.ELDERLY_PATIENT)
    db.add_all([admin, patient_user])
    db.flush()
    household = Household(created_by_user_id=admin.user_id, household_name=name)
    db.add(household)
    db.flush()
    patient = ElderlyPatient(
        user_id=patient_user.user_id,
        household_id=household.household_id,
        birthdate=datetime(1950, 1, 1).date(),
        sex=Sex.OTHER,
    )
    db.add(patient)
    db.flush()
    patient_user.test_household_id = household.household_id
    return admin, household, patient, patient_user


def assign(api_app, admin, household, caregiver, patient):
    return request(
        api_app,
        "POST",
        f"/api/v1/households/{household.household_id}/caregiver-assignments",
        headers=headers(admin),
        json={
            "caregiver_user_id": str(caregiver.user_id),
            "patient_id": str(patient.patient_id),
        },
    )


def test_assignment_rules_and_history(api_app, db_session):
    admin, household, patient, patient_user = build_household(db_session, "One")
    other_admin, other_household, other_patient, _ = build_household(db_session, "Two")
    caregiver = User(full_name="Caregiver", role=UserRole.CAREGIVER)
    db_session.add(caregiver)
    db_session.commit()

    wrong_role = assign(api_app, admin, household, patient_user, patient)
    assert wrong_role.status_code == 409
    cross_patient = assign(api_app, admin, household, caregiver, other_patient)
    assert cross_patient.status_code == 403

    created = assign(api_app, admin, household, caregiver, patient)
    assert created.status_code == 201
    duplicate = assign(api_app, admin, household, caregiver, patient)
    assert duplicate.status_code == 409
    cross_caregiver = assign(
        api_app, other_admin, other_household, caregiver, other_patient
    )
    assert cross_caregiver.status_code == 403

    assignment_id = created.json()["assignment_id"]
    unassigned = request(
        api_app,
        "POST",
        f"/api/v1/households/{household.household_id}/caregiver-assignments/"
        f"{assignment_id}/unassign",
        headers=headers(admin),
    )
    assert unassigned.status_code == 200
    stored = db_session.get(CaregiverPatientAssignment, assignment_id)
    assert stored is not None
    assert stored.unassigned_at is not None


def test_assignment_permissions_and_archived_entities(api_app, db_session):
    admin, household, patient, patient_user = build_household(db_session, "Home")
    outsider, _, _, _ = build_household(db_session, "Other")
    caregiver = User(full_name="Caregiver", role=UserRole.CAREGIVER)
    disabled = User(
        full_name="Disabled Admin",
        role=UserRole.CARE_ADMIN,
        account_status=AccountStatus.DISABLED,
    )
    db_session.add_all([caregiver, disabled])
    db_session.commit()

    assert assign(api_app, outsider, household, caregiver, patient).status_code == 403
    assert assign(api_app, caregiver, household, caregiver, patient).status_code == 403
    assert assign(api_app, patient_user, household, caregiver, patient).status_code == 403
    assert assign(api_app, disabled, household, caregiver, patient).status_code == 403

    household.household_status = HouseholdStatus.ARCHIVED
    household.archived_at = datetime.now(timezone.utc)
    db_session.commit()
    assert assign(api_app, admin, household, caregiver, patient).status_code == 403


def test_access_code_permissions_hashing_replacement_and_revocation(
    api_app, db_session
):
    admin, household, patient, patient_user = build_household(db_session, "Home")
    caregiver = User(full_name="Assigned", role=UserRole.CAREGIVER)
    unassigned = User(full_name="Unassigned", role=UserRole.CAREGIVER)
    db_session.add_all([caregiver, unassigned])
    db_session.commit()
    assert assign(api_app, admin, household, caregiver, patient).status_code == 201
    path = f"/api/v1/patients/{patient.patient_id}/access-codes"

    assert request(api_app, "POST", path, headers=headers(unassigned, bearer=True), json={}).status_code == 403
    assert request(api_app, "POST", path, headers=headers(patient_user, bearer=True), json={}).status_code == 403
    first = request(api_app, "POST", path, headers=headers(admin, bearer=True), json={})
    assert first.status_code == 201
    plaintext = first.json()["access_code"]
    stored = db_session.get(PatientAccessCode, first.json()["access_code_id"])
    assert plaintext not in stored.code_hash
    assert verify_access_code(plaintext, stored.code_hash)
    assert stored.access_code_selector == plaintext[:4]
    assert not verify_access_code("AAAA-AAAA-AAAA", stored.code_hash)

    replacement = request(api_app, "POST", path, headers=headers(caregiver, bearer=True), json={})
    assert replacement.status_code == 201
    db_session.refresh(stored)
    assert stored.revoked_at is not None
    replacement_id = replacement.json()["access_code_id"]
    revoked = request(
        api_app,
        "POST",
        f"{path}/{replacement_id}/revoke",
        headers=headers(caregiver, bearer=True),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    assert "access_code" not in revoked.json()


def test_access_code_validity_states(db_session, patient):
    now = datetime.now(timezone.utc)
    code = PatientAccessCode(
        patient_id=patient.patient_id,
        code_hash="not-plaintext",
        created_by_user_id=db_session.get(ElderlyPatient, patient.patient_id).user_id,
        expires_at=now + timedelta(hours=1),
    )
    assert code.is_valid(at=now)
    code.expires_at = now - timedelta(seconds=1)
    assert not code.is_valid(at=now)
    assert code.status == "EXPIRED"
    code.expires_at = now + timedelta(hours=1)
    code.used_at = now
    assert not code.is_valid(at=now)
    assert code.status == "USED"
    code.revoked_at = now
    assert not code.is_valid(at=now)
    assert code.status == "REVOKED"


def test_archived_patient_and_disabled_actor_cannot_manage_codes(api_app, db_session):
    admin, _, patient, _ = build_household(db_session, "Home")
    patient.archived_at = datetime.now(timezone.utc)
    db_session.commit()
    path = f"/api/v1/patients/{patient.patient_id}/access-codes"
    assert request(api_app, "POST", path, headers=headers(admin, bearer=True), json={}).status_code == 403
    patient.archived_at = None
    admin.account_status = AccountStatus.ARCHIVED
    db_session.commit()
    assert request(api_app, "POST", path, headers=headers(admin, bearer=True), json={}).status_code == 401


def test_code_bearer_permissions_for_issue_and_revoke(api_app, db_session):
    admin, household, patient, patient_user = build_household(db_session, "Home")
    outsider, _, _, _ = build_household(db_session, "Other")
    caregiver = User(full_name="Caregiver", role=UserRole.CAREGIVER)
    db_session.add(caregiver)
    db_session.commit()
    path = f"/api/v1/patients/{patient.patient_id}/access-codes"
    assert request(api_app, "POST", path, headers=headers(admin), json={}).status_code == 401
    issued = request(api_app, "POST", path, headers=headers(admin, bearer=True), json={})
    revoke = f"{path}/{issued.json()['access_code_id']}/revoke"
    for actor in (outsider, caregiver, patient_user):
        assert request(api_app, "POST", path, headers=headers(actor, bearer=True), json={}).status_code == 403
        assert request(api_app, "POST", revoke, headers=headers(actor, bearer=True)).status_code == 403
    assert assign(api_app, admin, household, caregiver, patient).status_code == 201
    assert request(api_app, "POST", revoke, headers=headers(caregiver, bearer=True)).status_code == 200
    assignment = db_session.scalar(select(CaregiverPatientAssignment).where(CaregiverPatientAssignment.caregiver_user_id == caregiver.user_id))
    assignment.unassigned_at = datetime.now(timezone.utc)
    db_session.commit()
    assert request(api_app, "POST", revoke, headers=headers(caregiver, bearer=True)).status_code == 403
    household.household_status = HouseholdStatus.INACTIVE
    db_session.commit()
    assert request(api_app, "POST", path, headers=headers(admin, bearer=True), json={}).status_code == 403
    assert request(api_app, "POST", revoke, headers=headers(admin, bearer=True)).status_code == 403
