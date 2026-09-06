import asyncio
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.security import hash_password
from app.core.config import Settings
from app.db.database import get_db
from app.households.model import Household, HouseholdStatus
from app.main import create_app
from app.users.model import User, UserRole

pytestmark = pytest.mark.integration


@pytest.fixture()
def api_app(db_session):
    app = create_app(Settings(environment="testing", database_url=None))

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return app


def post(app, path, payload):
    async def send():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.post(path, json=payload)

    return asyncio.run(send())


def make_household(db, *, status=HouseholdStatus.ACTIVE, archived_at=None):
    owner = User(
        full_name="Household Owner",
        email="owner@example.com",
        password_hash=hash_password("correct-password"),
        role=UserRole.CARE_ADMIN,
    )
    db.add(owner)
    db.flush()
    household = Household(
        created_by_user_id=owner.user_id,
        household_name="Maple Home",
        household_code="4V8F-29HC",
        household_status=status,
        archived_at=archived_at,
    )
    db.add(household)
    db.commit()
    return owner, household


@pytest.mark.parametrize("submitted", ["4v8f-29hc", "  4v8f29hc  "])
def test_public_validation_normalizes_code_and_returns_only_public_fields(
    api_app, db_session, submitted
):
    make_household(db_session)

    response = post(
        api_app,
        "/api/v1/auth/household/validate",
        {"household_code": submitted},
    )

    assert response.status_code == 200
    assert response.json() == {"valid": True, "household_name": "Maple Home"}
    assert "access_token" not in response.json()


@pytest.mark.parametrize(
    "status_value, archived_at",
    [
        (HouseholdStatus.INACTIVE, None),
        (HouseholdStatus.ARCHIVED, None),
        (HouseholdStatus.ACTIVE, datetime.now(timezone.utc)),
    ],
)
def test_unavailable_households_use_same_404(
    api_app, db_session, status_value, archived_at
):
    make_household(db_session, status=status_value, archived_at=archived_at)

    response = post(
        api_app,
        "/api/v1/auth/household/validate",
        {"household_code": "4V8F-29HC"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Household not found."}


@pytest.mark.parametrize("submitted", ["ZZZZ-ZZZZ", "malformed"])
def test_unknown_and_malformed_codes_use_generic_404(api_app, submitted):
    response = post(
        api_app,
        "/api/v1/auth/household/validate",
        {"household_code": submitted},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Household not found."}


def test_validation_does_not_replace_caregiver_login_checks(api_app, db_session):
    owner, household = make_household(db_session)
    validation = post(
        api_app,
        "/api/v1/auth/household/validate",
        {"household_code": "4V8F29HC"},
    )
    login = post(
        api_app,
        "/api/v1/auth/caregiver/login",
        {
            "household_code": "4V8F29HC",
            "email": owner.email,
            "password": "wrong-password",
        },
    )

    assert validation.status_code == 200
    assert login.status_code == 401
    assert login.json() == {
        "detail": "Invalid household code, email, or password."
    }

    household.household_status = HouseholdStatus.INACTIVE
    db_session.commit()
    unavailable_login = post(
        api_app,
        "/api/v1/auth/caregiver/login",
        {
            "household_code": "4V8F29HC",
            "email": owner.email,
            "password": "correct-password",
        },
    )
    assert unavailable_login.status_code == 401
