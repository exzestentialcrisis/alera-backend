import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth.dependencies import get_current_actor
from app.auth.security import create_access_token, hash_password
from app.auth.seed_demo_caregiver import seed_demo_caregiver
from app.core.config import Settings
from app.db.database import get_db
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.main import create_app
from app.patients.model import ElderlyPatient, Sex
from app.users.model import AccountStatus, User, UserRole

pytestmark = pytest.mark.integration
JWT_SECRET = "test-only-jwt-secret-that-is-long-and-random-enough"


@pytest.fixture()
def api_app(db_session):
    app = create_app(
        Settings(
            environment="testing",
            database_url=None,
            alera_jwt_secret=JWT_SECRET,
            alera_jwt_access_token_minutes=30,
        )
    )

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db

    @app.get("/_test/current-actor")
    async def current_actor(actor: User = Depends(get_current_actor)):
        return {"user_id": str(actor.user_id)}

    return app


def request(app, method, path, **kwargs):
    async def send():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def make_household(db, name, code):
    admin = User(
        full_name=f"{name} Admin",
        email=f"{name.lower()}-admin@example.com",
        password_hash=hash_password("admin-password"),
        role=UserRole.CARE_ADMIN,
    )
    patient_user = User(full_name=f"{name} Patient", role=UserRole.ELDERLY_PATIENT)
    db.add_all([admin, patient_user])
    db.flush()
    household = Household(
        created_by_user_id=admin.user_id,
        household_name=name,
        household_code=code,
    )
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
    return admin, household, patient, patient_user


def login(app, household_code, email, password):
    return request(
        app,
        "POST",
        "/api/v1/auth/caregiver/login",
        json={
            "household_code": household_code,
            "email": email,
            "password": password,
        },
    )


def test_assigned_caregiver_login_and_bearer_dependency(api_app, db_session):
    admin, household, patient, _ = make_household(db_session, "Home", "4V8F-29HC")
    caregiver = User(
        full_name="Demo Caregiver",
        email="caregiver@example.com",
        password_hash=hash_password("correct-password"),
        role=UserRole.CAREGIVER,
    )
    db_session.add(caregiver)
    db_session.flush()
    db_session.add(
        CaregiverPatientAssignment(
            caregiver_user_id=caregiver.user_id,
            patient_id=patient.patient_id,
            assigned_by_user_id=admin.user_id,
        )
    )
    db_session.commit()

    response = login(
        api_app, "4v8f-29hc", "CAREGIVER@example.com", "correct-password"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["actor"] == {
        "user_id": str(caregiver.user_id),
        "full_name": "Demo Caregiver",
        "role": "CAREGIVER",
        "household_id": str(household.household_id),
        "household_name": "Home",
        "household_code": "4V8F-29HC",
    }
    actor_response = request(
        api_app,
        "GET",
        "/_test/current-actor",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert actor_response.status_code == 200
    assert actor_response.json() == {"user_id": str(caregiver.user_id)}


def test_login_rejects_bad_password_wrong_household_and_inactive_assignment(
    api_app, db_session
):
    admin, household, patient, _ = make_household(db_session, "Home", "AAAA-BBBB")
    caregiver = User(
        full_name="Caregiver",
        email="caregiver@example.com",
        password_hash=hash_password("correct-password"),
        role=UserRole.CAREGIVER,
    )
    db_session.add(caregiver)
    db_session.flush()
    assignment = CaregiverPatientAssignment(
        caregiver_user_id=caregiver.user_id,
        patient_id=patient.patient_id,
        assigned_by_user_id=admin.user_id,
    )
    db_session.add(assignment)
    db_session.commit()

    assert (
        login(api_app, household.household_code, caregiver.email, "wrong").status_code
        == 401
    )
    assert (
        login(api_app, "ZZZZ-ZZZZ", caregiver.email, "correct-password").status_code
        == 401
    )
    assignment.unassigned_at = datetime.now(timezone.utc)
    db_session.commit()
    assert login(
        api_app, household.household_code, caregiver.email, "correct-password"
    ).status_code == 401


def test_care_admin_only_logs_into_owned_household(api_app, db_session):
    admin, household, _, _ = make_household(db_session, "Home", "AAAA-BBBB")
    _, other_household, _, _ = make_household(db_session, "Other", "CCCC-DDDD")
    db_session.commit()

    own = login(api_app, household.household_code, admin.email, "admin-password")
    assert own.status_code == 200
    assert own.json()["actor"]["role"] == "CARE_ADMIN"
    assert login(
        api_app, other_household.household_code, admin.email, "admin-password"
    ).status_code == 401


@pytest.mark.parametrize(
    "account_status", [AccountStatus.DISABLED, AccountStatus.ARCHIVED]
)
def test_disabled_and_archived_users_are_rejected(
    api_app, db_session, account_status
):
    admin, household, _, _ = make_household(db_session, "Home", "AAAA-BBBB")
    admin.account_status = account_status
    db_session.commit()
    assert login(
        api_app, household.household_code, admin.email, "admin-password"
    ).status_code == 401


def test_elderly_patient_is_rejected(api_app, db_session):
    _, household, _, patient_user = make_household(
        db_session, "Home", "AAAA-BBBB"
    )
    patient_user.email = "patient@example.com"
    patient_user.password_hash = hash_password("patient-password")
    db_session.commit()
    assert login(
        api_app, household.household_code, patient_user.email, "patient-password"
    ).status_code == 401


def test_bearer_dependency_rejects_invalid_and_expired_tokens(api_app, db_session):
    admin, household, _, _ = make_household(db_session, "Home", "AAAA-BBBB")
    db_session.commit()
    invalid = request(
        api_app,
        "GET",
        "/_test/current-actor",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert invalid.status_code == 401
    expired, _ = create_access_token(
        user_id=admin.user_id,
        household_id=household.household_id,
        secret=JWT_SECRET,
        expires_minutes=1,
        now=datetime.now(timezone.utc) - timedelta(minutes=2),
    )
    expired_response = request(
        api_app,
        "GET",
        "/_test/current-actor",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert expired_response.status_code == 401


def test_demo_seed_is_idempotent_and_creates_assignment(db_session):
    _, household, patient, _ = make_household(db_session, "Demo", "4V8F-29HC")
    db_session.commit()
    arguments = {
        "email": "demo-caregiver@example.com",
        "password": "demo-password",
        "patient_id": patient.patient_id,
    }
    first, first_household = seed_demo_caregiver(db_session, **arguments)
    second, second_household = seed_demo_caregiver(db_session, **arguments)

    assert second.user_id == first.user_id
    assert (
        second_household.household_id
        == first_household.household_id
        == household.household_id
    )
    assert db_session.scalar(
        select(func.count(User.user_id)).where(User.email == arguments["email"])
    ) == 1
    assignments = db_session.scalars(
        select(CaregiverPatientAssignment).where(
            CaregiverPatientAssignment.caregiver_user_id == first.user_id,
            CaregiverPatientAssignment.patient_id == patient.patient_id,
            CaregiverPatientAssignment.unassigned_at.is_(None),
        )
    ).all()
    assert len(assignments) == 1
