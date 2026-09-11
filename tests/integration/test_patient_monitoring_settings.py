from uuid import UUID

import pytest

from app.patients.model import ElderlyPatient
from tests.integration.test_caregiver_auth import api_app, make_household, request
from tests.integration.test_patient_creation import headers

pytestmark = pytest.mark.integration


def setup_scope(db):
    admin, household, patient, _ = make_household(db, "Home", "AAAA-BBBB")
    from app.household_access.model import CaregiverPatientAssignment
    from app.users.model import User, UserRole

    caregiver = User(full_name="Caregiver", role=UserRole.CAREGIVER)
    db.add(caregiver)
    db.flush()
    db.add(CaregiverPatientAssignment(
        caregiver_user_id=caregiver.user_id,
        patient_id=patient.patient_id,
        assigned_by_user_id=admin.user_id,
    ))
    db.commit()
    return admin, caregiver, household, patient


def endpoint(patient):
    return f"/api/v1/patients/{patient.patient_id}/monitoring-settings"


def test_defaults_and_partial_update_for_authorized_roles(api_app, db_session):
    admin, caregiver, household, patient = setup_scope(db_session)
    path = endpoint(patient)

    response = request(api_app, "PATCH", path, headers=headers(caregiver, household), json={"normal_hr_min": 55})
    assert response.status_code == 200
    assert response.json()["normal_hr_min"] == 55
    assert response.json()["threshold_mode"] == "CUSTOM"

    response = request(api_app, "PATCH", path, headers=headers(admin, household), json={
        "normal_hr_min": 60, "normal_hr_max": 100,
        "usual_spo2_min": 95, "usual_spo2_max": None,
    })
    assert response.status_code == 200
    assert response.json()["threshold_mode"] == "DEFAULT"

    detail = request(api_app, "GET", f"/api/v1/patients/{patient.patient_id}", headers=headers(admin, household))
    assert detail.status_code == 200
    assert detail.json()["usual_spo2_max"] is None


def test_all_monitoring_settings_update_and_explicit_clear(api_app, db_session):
    admin, _, household, patient = setup_scope(db_session)
    response = request(api_app, "PATCH", endpoint(patient), headers=headers(admin, household), json={
        "normal_hr_min": 55, "normal_hr_max": 105,
        "usual_spo2_min": 94, "usual_spo2_max": 100,
    })
    assert response.status_code == 200
    assert response.json()["normal_hr_min"] == 55
    assert response.json()["usual_spo2_max"] == 100

    response = request(api_app, "PATCH", endpoint(patient), headers=headers(admin, household), json={"usual_spo2_max": None})
    assert response.status_code == 200
    assert response.json()["usual_spo2_max"] is None


@pytest.mark.parametrize("payload", [
    {},
    {"normal_hr_min": 0},
    {"normal_hr_max": -1},
    {"usual_spo2_min": -1},
    {"usual_spo2_max": 101},
    {"normal_hr_min": 101},
    {"usual_spo2_min": 99, "usual_spo2_max": 98},
])
def test_invalid_monitoring_settings_are_422(api_app, db_session, payload):
    admin, _, household, patient = setup_scope(db_session)
    response = request(api_app, "PATCH", endpoint(patient), headers=headers(admin, household), json=payload)
    assert response.status_code == 422
    stored = db_session.get(ElderlyPatient, UUID(str(patient.patient_id)))
    assert (stored.normal_hr_min, stored.normal_hr_max, stored.usual_spo2_min, stored.usual_spo2_max) == (60, 100, 95, None)


def test_partial_update_compares_existing_counterpart_and_out_of_scope_is_hidden(api_app, db_session):
    admin, caregiver, household, patient = setup_scope(db_session)
    assert request(api_app, "PATCH", endpoint(patient), headers=headers(admin, household), json={"normal_hr_min": 101}).status_code == 422

    _, other_household, other_patient, _ = make_household(db_session, "Other", "CCCC-DDDD")
    db_session.commit()
    assert request(api_app, "PATCH", endpoint(other_patient), headers=headers(caregiver, household), json={"normal_hr_min": 55}).status_code == 404
