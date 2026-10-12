# ruff: noqa: F401, F811
from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.time import utc_now
from app.household_access.model import PatientAccessCode
from app.patients.model import ElderlyPatient
from tests.integration.test_caregiver_auth import api_app as caregiver_api_app, request
from tests.integration.test_patient_creation import headers
from tests.integration.test_patient_relationships import setup_shared_patient

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", ["first", "admin"])
def test_archive_preserves_patient_hides_reads_revokes_codes_and_allows_retry(
    caregiver_api_app, db_session, role
):
    scope = setup_shared_patient(db_session)
    patient = scope["patient"]
    code = PatientAccessCode(
        patient_id=patient.patient_id,
        code_hash="test-hash",
        created_by_user_id=scope["admin"].user_id,
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(code)
    db_session.commit()
    path = f"/api/v1/patients/{patient.patient_id}"
    auth = headers(scope[role], scope["household"])
    response = request(caregiver_api_app, "POST", path + "/archive", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == str(patient.patient_id)
    assert body["archived_at"] is not None
    db_session.refresh(patient)
    db_session.refresh(code)
    assert db_session.get(ElderlyPatient, patient.patient_id) is not None
    assert code.revoked_at == patient.archived_at
    assert scope["first_assignment"].unassigned_at is None
    assert request(caregiver_api_app, "POST", path + "/archive", headers=auth).json() == body
    for role in ("first", "second", "admin"):
        auth = headers(scope[role], scope["household"])
        assert request(caregiver_api_app, "GET", path, headers=auth).status_code == 404
        listing = request(caregiver_api_app, "GET", "/api/v1/patients", headers=auth)
        assert listing.status_code == 200
        assert listing.json()["items"] == []


def test_archive_outside_scope_or_missing_does_not_modify_patient(
    caregiver_api_app, db_session
):
    scope = setup_shared_patient(db_session)
    for patient_id in (scope["patient"].patient_id, uuid4()):
        response = request(
            caregiver_api_app, "POST", f"/api/v1/patients/{patient_id}/archive",
            headers=headers(scope["outsider"], scope["household"]),
        )
        assert response.status_code == 404
    db_session.refresh(scope["patient"])
    assert scope["patient"].archived_at is None


def test_archive_requires_authentication(caregiver_api_app, db_session):
    scope = setup_shared_patient(db_session)
    response = request(
        caregiver_api_app, "POST", f'/api/v1/patients/{scope["patient"].patient_id}/archive'
    )
    assert response.status_code == 401
