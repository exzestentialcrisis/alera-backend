from datetime import timedelta

import pytest
from sqlalchemy import select

from app.auth.errors import AuthenticationError
from app.auth.schema import PatientAccessRequest
from app.auth.service import authenticate_patient
from app.core.config import Settings
from app.core.time import utc_now
from app.household_access import service
from app.household_access.model import PatientAccessCode
from app.household_access.security import access_code_selector, hash_access_code
from app.household_access.service import issue_access_code
from tests.integration.test_caregiver_auth import JWT_SECRET, make_household

pytestmark = pytest.mark.integration


def test_selector_collision_and_complete_code_collision_retry(db_session, monkeypatch):
    admin, _, patient, _ = make_household(db_session, "Home", "AAAA-BBBB")
    duplicate = "ABCD-EFGH-JKMN"
    db_session.add(PatientAccessCode(
        patient_id=patient.patient_id, code_hash=hash_access_code(duplicate),
        access_code_selector="ABCD", created_by_user_id=admin.user_id,
        expires_at=utc_now() + timedelta(hours=1),
    ))
    db_session.commit()
    generated = iter([duplicate, "ABCD-2345-6789"])
    monkeypatch.setattr(service, "generate_access_code", lambda: next(generated))
    code, readable = issue_access_code(db_session, patient.patient_id, admin, 24)
    assert readable == "ABCD-2345-6789"
    assert code.access_code_selector == "ABCD"


def test_selector_candidates_resolve_complete_code_and_bound_work(db_session, monkeypatch):
    admin, _, patient, _ = make_household(db_session, "Home", "AAAA-BBBB")
    codes = ["ABCD-EFGH-JKMN", "ABCD-2345-6789"]
    for value in codes:
        db_session.add(PatientAccessCode(
            patient_id=patient.patient_id, code_hash=hash_access_code(value),
            access_code_selector=access_code_selector(value), created_by_user_id=admin.user_id,
            expires_at=utc_now() + timedelta(hours=1),
        ))
    db_session.commit()
    result = authenticate_patient(
        db_session, PatientAccessRequest(access_code=codes[1]),
        Settings(alera_jwt_secret=JWT_SECRET),
    )
    assert result["actor"]["household_id"]
    stored = db_session.scalars(select(PatientAccessCode).where(
        PatientAccessCode.access_code_selector == "ABCD"
    )).all()
    assert sum(code.used_at is not None for code in stored) == 1


def test_oversized_credential_is_generic_failure(db_session):
    with pytest.raises(AuthenticationError, match="Invalid access code"):
        authenticate_patient(db_session, PatientAccessRequest(access_code="A" * 10000), Settings(alera_jwt_secret=JWT_SECRET))
