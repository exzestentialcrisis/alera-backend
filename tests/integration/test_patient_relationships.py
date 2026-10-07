# ruff: noqa: F401, F811
from uuid import UUID

import pytest
from sqlalchemy import select

from app.household_access.model import CaregiverPatientAssignment
from app.patients.model import ElderlyPatient
from app.users.model import User, UserRole
from tests.integration.test_caregiver_auth import (
    api_app as caregiver_api_app,
)
from tests.integration.test_caregiver_auth import (
    make_household,
    request,
)
from tests.integration.test_patient_creation import headers

pytestmark = pytest.mark.integration


def assign(db, caregiver, patient, admin, relationship):
    assignment = CaregiverPatientAssignment(
        caregiver_user_id=caregiver.user_id,
        patient_id=patient.patient_id,
        assigned_by_user_id=admin.user_id,
        relationship_label=relationship,
    )
    db.add(assignment)
    db.flush()
    return assignment


def update_payload(*, relationship_label, full_name="Updated Patient"):
    return {
        "full_name": full_name,
        "birthdate": "1950-01-02",
        "sex": "FEMALE",
        "phone_number": "09123456789",
        "address_or_room": "Room 12",
        "emergency_contact_name": "Emergency Contact",
        "emergency_contact_phone": "09170000000",
        "known_conditions": "Hypertension",
        "medications": "Medication",
        "monitoring_notes": "Check twice daily",
        "relationship_label": relationship_label,
    }


def setup_shared_patient(db):
    admin, household, patient, patient_user = make_household(
        db,
        "Home",
        "AAAA-BBBB",
    )
    first = User(full_name="First Caregiver", role=UserRole.CAREGIVER)
    second = User(full_name="Second Caregiver", role=UserRole.CAREGIVER)
    outsider = User(full_name="Outsider", role=UserRole.CAREGIVER)
    db.add_all([first, second, outsider])
    db.flush()

    first_assignment = assign(db, first, patient, admin, "Mother")
    second_assignment = assign(db, second, patient, admin, "Client")
    db.commit()

    return {
        "admin": admin,
        "household": household,
        "patient": patient,
        "patient_user": patient_user,
        "first": first,
        "second": second,
        "outsider": outsider,
        "first_assignment": first_assignment,
        "second_assignment": second_assignment,
    }


def test_creation_normalizes_and_persists_relationship(caregiver_api_app, db_session):
    scope = setup_shared_patient(db_session)

    response = request(
        caregiver_api_app,
        "POST",
        "/api/v1/patients",
        headers=headers(scope["first"], scope["household"]),
        json={
            "full_name": "New Patient",
            "relationship_label": "  Grand   Mother  ",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["relationship_label"] == "Grand Mother"

    assignment = db_session.scalar(
        select(CaregiverPatientAssignment).where(
            CaregiverPatientAssignment.caregiver_user_id == scope["first"].user_id,
            CaregiverPatientAssignment.patient_id == UUID(body["patient_id"]),
            CaregiverPatientAssignment.unassigned_at.is_(None),
        )
    )
    assert assignment is not None
    assert assignment.relationship_label == "Grand Mother"


def test_each_caregiver_reads_their_own_relationship(caregiver_api_app, db_session):
    scope = setup_shared_patient(db_session)
    patient_id = scope["patient"].patient_id

    first_detail = request(
        caregiver_api_app,
        "GET",
        f"/api/v1/patients/{patient_id}",
        headers=headers(scope["first"], scope["household"]),
    )
    second_detail = request(
        caregiver_api_app,
        "GET",
        f"/api/v1/patients/{patient_id}",
        headers=headers(scope["second"], scope["household"]),
    )
    first_list = request(
        caregiver_api_app,
        "GET",
        "/api/v1/patients",
        headers=headers(scope["first"], scope["household"]),
    )

    assert first_detail.status_code == 200
    assert second_detail.status_code == 200
    assert first_detail.json()["relationship_label"] == "Mother"
    assert second_detail.json()["relationship_label"] == "Client"
    assert first_list.json()["items"][0]["relationship_label"] == "Mother"


def test_patch_updates_profile_and_only_actor_relationship(
    caregiver_api_app, db_session
):
    scope = setup_shared_patient(db_session)
    patient_id = scope["patient"].patient_id

    response = request(
        caregiver_api_app,
        "PATCH",
        f"/api/v1/patients/{patient_id}",
        headers=headers(scope["first"], scope["household"]),
        json=update_payload(relationship_label="  Grand   Mother  "),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Updated Patient"
    assert body["relationship_label"] == "Grand Mother"
    assert body["monitoring_notes"] == "Check twice daily"

    db_session.expire_all()
    first_assignment = db_session.get(
        CaregiverPatientAssignment,
        scope["first_assignment"].assignment_id,
    )
    second_assignment = db_session.get(
        CaregiverPatientAssignment,
        scope["second_assignment"].assignment_id,
    )
    patient = db_session.get(ElderlyPatient, patient_id)
    patient_user = db_session.get(User, patient.user_id)

    assert first_assignment.relationship_label == "Grand Mother"
    assert second_assignment.relationship_label == "Client"
    assert patient_user.full_name == "Updated Patient"
    assert patient.address_or_room == "Room 12"

    second_detail = request(
        caregiver_api_app,
        "GET",
        f"/api/v1/patients/{patient_id}",
        headers=headers(scope["second"], scope["household"]),
    )
    assert second_detail.json()["relationship_label"] == "Client"
    assert second_detail.json()["full_name"] == "Updated Patient"


def test_blank_relationship_becomes_null(caregiver_api_app, db_session):
    scope = setup_shared_patient(db_session)
    patient_id = scope["patient"].patient_id

    response = request(
        caregiver_api_app,
        "PATCH",
        f"/api/v1/patients/{patient_id}",
        headers=headers(scope["first"], scope["household"]),
        json=update_payload(relationship_label="   "),
    )

    assert response.status_code == 200
    assert response.json()["relationship_label"] is None


def test_unassigned_caregiver_cannot_read_or_update(caregiver_api_app, db_session):
    scope = setup_shared_patient(db_session)
    patient_id = scope["patient"].patient_id
    outsider_headers = headers(scope["outsider"], scope["household"])

    assert (
        request(
            caregiver_api_app,
            "GET",
            f"/api/v1/patients/{patient_id}",
            headers=outsider_headers,
        ).status_code
        == 404
    )

    assert (
        request(
            caregiver_api_app,
            "PATCH",
            f"/api/v1/patients/{patient_id}",
            headers=outsider_headers,
            json=update_payload(relationship_label="Client"),
        ).status_code
        == 404
    )


def test_admin_can_update_shared_profile_but_not_set_relationship(
    caregiver_api_app,
    db_session,
):
    scope = setup_shared_patient(db_session)
    patient_id = scope["patient"].patient_id
    admin_headers = headers(scope["admin"], scope["household"])

    allowed = request(
        caregiver_api_app,
        "PATCH",
        f"/api/v1/patients/{patient_id}",
        headers=admin_headers,
        json=update_payload(
            relationship_label=None,
            full_name="Admin Updated",
        ),
    )
    assert allowed.status_code == 200
    assert allowed.json()["full_name"] == "Admin Updated"
    assert allowed.json()["relationship_label"] is None

    forbidden = request(
        caregiver_api_app,
        "PATCH",
        f"/api/v1/patients/{patient_id}",
        headers=admin_headers,
        json=update_payload(relationship_label="Mother"),
    )
    assert forbidden.status_code == 403


@pytest.mark.parametrize(
    "extra_fields",
    [
        {"relationship_label": "x" * 51},
        {"normal_hr_min": 40},
        {"baseline_heart_rate": "55"},
    ],
)
def test_patch_rejects_invalid_or_monitoring_fields(
    caregiver_api_app,
    db_session,
    extra_fields,
):
    scope = setup_shared_patient(db_session)
    payload = update_payload(relationship_label="Mother")
    payload.update(extra_fields)

    response = request(
        caregiver_api_app,
        "PATCH",
        f"/api/v1/patients/{scope['patient'].patient_id}",
        headers=headers(scope["first"], scope["household"]),
        json=payload,
    )

    assert response.status_code == 422
