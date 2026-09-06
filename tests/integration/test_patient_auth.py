from datetime import timedelta
import stat

import pytest

from app.auth.security import decode_access_token
from app.core.time import utc_now
from app.household_access.service import issue_access_code
from app.users.model import AccountStatus
from scripts.demo_patient_access import DEMO_PATIENT_ID, issue_demo_code
from tests.integration.test_caregiver_auth import (
    JWT_SECRET, api_app, make_household, request,
)

pytestmark = pytest.mark.integration


def setup_code(db):
    admin, household, patient, user = make_household(db, "Alera Test", "AAAA-BBBB")
    user.full_name = "Alera Test Patient"
    patient.patient_id = DEMO_PATIENT_ID
    db.flush()
    code, readable = issue_access_code(db, patient.patient_id, admin, 24)
    db.commit()
    return household, patient, user, code, readable


def enroll(app, code):
    return request(app, "POST", "/api/v1/auth/patient/access", json={
        "access_code": code,
    })


def test_patient_enrollment_and_replay(api_app, db_session):
    household, patient, user, code, readable = setup_code(db_session)
    response = enroll(api_app, readable.lower().replace("-", ""))
    assert response.status_code == 200
    body = response.json()
    assert body["actor"]["role"] == "ELDERLY_PATIENT"
    assert body["actor"]["full_name"] == "Alera Test Patient"
    assert body["token_type"] == "bearer"
    assert set(body) == {"access_token", "token_type", "expires_at", "actor"}
    claims = decode_access_token(body["access_token"], secret=JWT_SECRET)
    assert claims["sub"] == str(user.user_id)
    assert claims["household_id"] == str(household.household_id)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert request(api_app, "GET", "/_test/current-actor", headers=headers).status_code == 200
    assert request(api_app, "GET", "/api/v1/alerts", headers=headers).status_code == 403
    assert enroll(api_app, readable).status_code == 401
    db_session.refresh(code)
    assert code.used_at is not None


@pytest.mark.parametrize("failure", ["unknown", "revoked", "used", "expired", "archived", "disabled", "inactive", "unicode", "malformed"])
def test_generic_patient_failures(api_app, db_session, failure):
    household, patient, user, code, readable = setup_code(db_session)
    if failure == "unknown": readable = "AAAA-AAAA-AAAA"
    if failure == "unicode": readable = "你好"
    if failure == "malformed": readable = "AAAA-AAAA"
    if failure == "revoked": code.revoked_at = utc_now()
    if failure == "used": code.used_at = utc_now()
    if failure == "expired": code.expires_at = utc_now() - timedelta(seconds=1)
    if failure == "archived": patient.archived_at = utc_now()
    if failure == "disabled": user.account_status = AccountStatus.DISABLED
    if failure == "inactive": user.account_status = AccountStatus.INACTIVE
    db_session.commit()
    response = enroll(api_app, readable)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid access code."}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("change", ["disabled", "archived_user", "archived_patient", "household", "moved"])
def test_patient_session_rechecks_state(api_app, db_session, change):
    household, patient, user, _, readable = setup_code(db_session)
    token = enroll(api_app, readable).json()["access_token"]
    if change == "disabled": user.account_status = AccountStatus.DISABLED
    if change == "archived_user": user.account_status = AccountStatus.ARCHIVED
    if change == "archived_patient": patient.archived_at = utc_now()
    if change == "household": household.archived_at = utc_now()
    if change == "moved":
        _, other, _, _ = make_household(db_session, "Other", "CCCC-DDDD")
        patient.household_id = other.household_id
    db_session.commit()
    assert request(api_app, "GET", "/_test/current-actor", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_demo_requires_development_and_explicit_reset(db_session):
    household, patient, user, code, readable = setup_code(db_session)
    with pytest.raises(RuntimeError, match="development"):
        issue_demo_code(db_session, environment="production", reset=True)
    with pytest.raises(RuntimeError, match="--reset"):
        issue_demo_code(db_session, environment="development", reset=False)
    fresh = issue_demo_code(db_session, environment="development", reset=True)
    assert fresh != readable
    assert code.revoked_at is not None


def test_demo_code_output_is_private(tmp_path):
    from scripts.demo_patient_access import write_private_code

    output = tmp_path / "demo-code.txt"
    write_private_code(str(output), "ABCD-EFGH-JKMN")
    assert output.read_text() == "ABCD-EFGH-JKMN\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_concurrent_redemption_has_one_winner(db_session, integration_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from app.auth.errors import AuthenticationError
    from app.auth.schema import PatientAccessRequest
    from app.auth.service import authenticate_patient
    from app.core.config import Settings
    from app.auth import service

    household, _, _, _, readable = setup_code(db_session)
    barrier = Barrier(2)
    original = service.verify_access_code

    def synchronized_verify(*args):
        result = original(*args)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(service, "verify_access_code", synchronized_verify)
    payload = PatientAccessRequest(access_code=readable)

    def redeem():
        with Session(integration_engine) as db:
            try:
                authenticate_patient(db, payload, Settings(alera_jwt_secret=JWT_SECRET))
                db.commit()
                return 200
            except AuthenticationError:
                db.rollback()
                return 401

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: redeem(), range(2))) == [200, 401]
