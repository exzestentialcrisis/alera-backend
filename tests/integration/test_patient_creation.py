from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.auth.security import create_access_token
from app.core.time import utc_now
from app.household_access.model import CaregiverPatientAssignment, PatientAccessCode
from app.households.model import HouseholdStatus
from app.patients.model import ElderlyPatient
from app.users.model import User, UserRole
from tests.integration.test_caregiver_auth import (
    JWT_SECRET, api_app, make_household, request,
)

pytestmark = pytest.mark.integration


def headers(actor, household):
    token, _ = create_access_token(
        user_id=actor.user_id, household_id=household.household_id,
        secret=JWT_SECRET, expires_minutes=30,
    )
    return {"Authorization": f"Bearer {token}"}


def fixture(db):
    admin, household, patient, patient_user = make_household(db, "Home", "AAAA-BBBB")
    caregiver = User(full_name="Caregiver", role=UserRole.CAREGIVER)
    db.add(caregiver)
    db.flush()
    assignment = CaregiverPatientAssignment(
        caregiver_user_id=caregiver.user_id, patient_id=patient.patient_id,
        assigned_by_user_id=admin.user_id,
    )
    db.add(assignment)
    db.commit()
    return admin, household, caregiver, patient_user, assignment


@pytest.mark.parametrize("role", ["caregiver", "admin"])
def test_create_and_later_enroll(api_app, db_session, role):
    admin, household, caregiver, _, _ = fixture(db_session)
    actor = caregiver if role == "caregiver" else admin
    response = request(api_app, "POST", "/api/v1/patients",
                       headers=headers(actor, household), json={"full_name": " New Patient "})
    assert response.status_code == 201
    body = response.json()
    assert body["full_name"] == "New Patient"
    assert body["household_id"] == str(household.household_id)
    assert body["account_status"] == "ACTIVE"
    assert body["archived_at"] is None
    assert body["created_at"]
    patient_id = UUID(body["patient_id"])
    user = db_session.get(User, UUID(body["user_id"]))
    assert user.role is UserRole.ELDERLY_PATIENT
    assert user.email is None and user.password_hash is None
    patient = db_session.get(ElderlyPatient, patient_id)
    assert patient.birthdate is None and patient.sex is None
    assert (patient.normal_hr_min, patient.normal_hr_max, patient.usual_spo2_min) == (60, 100, 95)
    assignment = db_session.scalar(select(CaregiverPatientAssignment).where(
        CaregiverPatientAssignment.patient_id == patient_id))
    if role == "caregiver":
        assert assignment.caregiver_user_id == caregiver.user_id
        assert assignment.assigned_by_user_id == caregiver.user_id
        assert assignment.unassigned_at is None
        assert body["assignment"]["assignment_id"] == str(assignment.assignment_id)
    else:
        assert assignment is None and body["assignment"] is None
    assert db_session.scalar(select(func.count()).select_from(PatientAccessCode)) == 0

    code = request(api_app, "POST", f"/api/v1/patients/{patient_id}/access-codes",
                   headers=headers(actor, household), json={})
    assert code.status_code == 201
    enrolled = request(api_app, "POST", "/api/v1/auth/patient/access", json={
        "household_code": household.household_code,
        "access_code": code.json()["access_code"],
    })
    assert enrolled.status_code == 200
    assert enrolled.json()["actor"]["user_id"] == body["user_id"]


def test_profile_persists_without_changing_thresholds(api_app, db_session):
    admin, household, _, _, _ = fixture(db_session)
    payload = {
        "full_name": "Patient", "birthdate": "1950-01-02", "sex": "FEMALE",
        "phone_number": "09123456789", "address_or_room": "Room 2",
        "emergency_contact_name": "Contact", "emergency_contact_phone": "+639123456789",
        "known_conditions": "Condition", "medications": "Medication",
        "baseline_heart_rate": "72.50", "baseline_spo2": "98.00",
        "monitoring_notes": "Notes",
    }
    response = request(api_app, "POST", "/api/v1/patients",
                       headers=headers(admin, household), json=payload)
    assert response.status_code == 201
    patient = db_session.get(ElderlyPatient, UUID(response.json()["patient_id"]))
    assert patient.health_notes == "Notes"
    for field in ("address_or_room", "emergency_contact_name", "emergency_contact_phone",
                  "known_conditions", "medications"):
        assert getattr(patient, field) == payload[field]
    assert str(patient.baseline_heart_rate) == "72.50"
    assert str(patient.baseline_spo2) == "98.00"
    assert patient.usual_spo2_min == 95
    assert db_session.get(User, patient.user_id).phone_number == payload["phone_number"]


@pytest.mark.parametrize("role,status", [("none", 401), ("patient", 403)])
def test_auth_rejected(api_app, db_session, role, status):
    _, household, _, patient_user, _ = fixture(db_session)
    count = db_session.scalar(select(func.count()).select_from(User))
    response = request(api_app, "POST", "/api/v1/patients", json={"full_name": "Patient"},
                       headers={} if role == "none" else headers(patient_user, household))
    assert response.status_code == status
    assert db_session.scalar(select(func.count()).select_from(User)) == count


@pytest.mark.parametrize("change", ["unassigned", "inactive", "archived", "wrong_caregiver_household", "wrong_admin_household"])
def test_household_scope_rechecked(api_app, db_session, change):
    admin, household, caregiver, _, assignment = fixture(db_session)
    actor = caregiver
    if change == "unassigned":
        assignment.unassigned_at = utc_now()
    elif change == "inactive":
        household.household_status = HouseholdStatus.INACTIVE
    elif change == "archived":
        household.archived_at = utc_now()
    else:
        _, household, _, _ = make_household(db_session, "Other", "CCCC-DDDD")
        if change == "wrong_admin_household":
            actor = admin
    db_session.commit()
    count = db_session.scalar(select(func.count()).select_from(User))
    response = request(api_app, "POST", "/api/v1/patients",
                       headers=headers(actor, household), json={"full_name": "Patient"})
    assert response.status_code == 403
    assert db_session.scalar(select(func.count()).select_from(User)) == count


def test_other_household_cannot_manage_or_see_alerts(api_app, db_session):
    admin, household, caregiver, _, _ = fixture(db_session)
    response = request(api_app, "POST", "/api/v1/patients",
                       headers=headers(caregiver, household), json={"full_name": "Patient"})
    patient_id = response.json()["patient_id"]
    other_admin, other_household, other_patient, _ = make_household(db_session, "Other", "CCCC-DDDD")
    other = User(full_name="Other caregiver", role=UserRole.CAREGIVER)
    db_session.add(other)
    db_session.flush()
    db_session.add(CaregiverPatientAssignment(
        caregiver_user_id=other.user_id, patient_id=other_patient.patient_id,
        assigned_by_user_id=other_admin.user_id,
    ))
    db_session.commit()
    code = request(api_app, "POST", f"/api/v1/patients/{patient_id}/access-codes",
                   headers=headers(caregiver, household), json={}).json()
    assert request(api_app, "POST", f"/api/v1/patients/{patient_id}/access-codes",
                   headers=headers(other, other_household), json={}).status_code == 403
    assert request(api_app, "POST",
                   f"/api/v1/patients/{patient_id}/access-codes/{code['access_code_id']}/revoke",
                   headers=headers(other, other_household)).status_code == 403
    # Create through the normal rule flow, then verify real alert access isolation.
    from app.health_events.schema import HealthEventCreate
    from app.health_events.service import create_health_event
    create_health_event(db_session, HealthEventCreate(
        patient_id=patient_id, metric_type="HEART_RATE", numeric_value=154,
        recorded_at=utc_now(), validation_status="VALID_REALTIME",
    ))
    own = request(api_app, "GET", f"/api/v1/alerts?patient_id={patient_id}",
                  headers=headers(caregiver, household)).json()
    assert own["total"] == 1
    foreign = headers(other, other_household)
    assert request(api_app, "GET", f"/api/v1/alerts?patient_id={patient_id}",
                   headers=foreign).json()["total"] == 0
    assert request(api_app, "GET", f"/api/v1/alerts/{own['items'][0]['alert_id']}",
                   headers=foreign).status_code == 404


def test_failure_rolls_back_user_and_patient(api_app, db_session, monkeypatch):
    _, household, caregiver, _, _ = fixture(db_session)
    from app.patients import service
    original = service.CaregiverPatientAssignment
    def fail(**kwargs):
        raise RuntimeError("simulated assignment failure")
    monkeypatch.setattr(service, "CaregiverPatientAssignment", type(
        "BrokenAssignment", (), {
            "assignment_id": original.assignment_id, "patient_id": original.patient_id,
            "caregiver_user_id": original.caregiver_user_id,
            "unassigned_at": original.unassigned_at,
            "__new__": lambda cls, **kwargs: fail(**kwargs),
        }))
    count = db_session.scalar(select(func.count()).select_from(User))
    with pytest.raises(RuntimeError, match="simulated"):
        request(api_app, "POST", "/api/v1/patients",
                headers=headers(caregiver, household), json={"full_name": "Patient"})
    assert db_session.scalar(select(func.count()).select_from(User)) == count
