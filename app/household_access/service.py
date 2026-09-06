from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.household_access.errors import (
    AccessConflictError,
    AccessForbiddenError,
    AccessNotFoundError,
)
from app.household_access.model import (
    CaregiverPatientAssignment,
    PatientAccessCode,
)
from app.household_access.security import (
    access_code_selector,
    generate_access_code,
    hash_access_code,
    verify_access_code,
)
from app.households.model import Household, HouseholdStatus
from app.patients.model import ElderlyPatient
from app.users.model import AccountStatus, User, UserRole


def _require_active_actor(actor: User) -> None:
    if actor.account_status in {AccountStatus.DISABLED, AccountStatus.ARCHIVED}:
        raise AccessForbiddenError("Actor is not permitted to manage household access.")


def _admin_household(db: Session, household_id: UUID, actor: User) -> Household:
    _require_active_actor(actor)
    household = db.get(Household, household_id)
    if (
        actor.role is not UserRole.CARE_ADMIN
        or household is None
        or household.created_by_user_id != actor.user_id
        or household.household_status is HouseholdStatus.ARCHIVED
        or household.archived_at is not None
    ):
        raise AccessForbiddenError("Actor is not permitted to manage this household.")
    return household


def _patient_for_code_management(
    db: Session, patient_id: UUID, actor: User
) -> ElderlyPatient:
    _require_active_actor(actor)
    if actor.account_status is not AccountStatus.ACTIVE or actor.role is UserRole.ELDERLY_PATIENT:
        raise AccessForbiddenError("Actor is not permitted to manage this patient.")
    patient = db.get(ElderlyPatient, patient_id)
    if patient is None:
        raise AccessNotFoundError("Patient not found.")
    household = db.get(Household, patient.household_id)
    archived = (
        patient.archived_at is not None
        or household is None
        or household.household_status is not HouseholdStatus.ACTIVE
        or household.archived_at is not None
    )
    permitted = False
    if not archived and actor.role is UserRole.CARE_ADMIN:
        permitted = household.created_by_user_id == actor.user_id
    elif not archived and actor.role is UserRole.CAREGIVER:
        permitted = db.scalar(
            select(CaregiverPatientAssignment.assignment_id).where(
                CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
                CaregiverPatientAssignment.patient_id == patient.patient_id,
                CaregiverPatientAssignment.unassigned_at.is_(None),
            )
        ) is not None
    if not permitted:
        raise AccessForbiddenError("Actor is not permitted to manage this patient.")
    return patient


def create_assignment(
    db: Session,
    household_id: UUID,
    caregiver_user_id: UUID,
    patient_id: UUID,
    actor: User,
) -> CaregiverPatientAssignment:
    _admin_household(db, household_id, actor)
    caregiver = db.get(User, caregiver_user_id)
    if caregiver is None or caregiver.role is not UserRole.CAREGIVER:
        raise AccessConflictError("The assigned user must have the CAREGIVER role.")
    if caregiver.account_status in {AccountStatus.DISABLED, AccountStatus.ARCHIVED}:
        raise AccessConflictError("The caregiver account is disabled or archived.")
    patient = db.get(ElderlyPatient, patient_id)
    if (
        patient is None
        or patient.household_id != household_id
        or patient.archived_at is not None
    ):
        raise AccessForbiddenError("Patient is not available in this household.")

    other_household = db.scalar(
        select(ElderlyPatient.household_id)
        .join(
            CaregiverPatientAssignment,
            CaregiverPatientAssignment.patient_id == ElderlyPatient.patient_id,
        )
        .where(
            CaregiverPatientAssignment.caregiver_user_id == caregiver_user_id,
            ElderlyPatient.household_id != household_id,
        )
        .limit(1)
    )
    if other_household is not None:
        raise AccessForbiddenError("Cross-household caregiver assignment is not allowed.")
    duplicate = db.scalar(
        select(CaregiverPatientAssignment.assignment_id).where(
            CaregiverPatientAssignment.caregiver_user_id == caregiver_user_id,
            CaregiverPatientAssignment.patient_id == patient_id,
            CaregiverPatientAssignment.unassigned_at.is_(None),
        )
    )
    if duplicate is not None:
        raise AccessConflictError("An active assignment already exists.")
    assignment = CaregiverPatientAssignment(
        caregiver_user_id=caregiver_user_id,
        patient_id=patient_id,
        assigned_by_user_id=actor.user_id,
    )
    db.add(assignment)
    try:
        db.flush()
    except IntegrityError as exc:
        raise AccessConflictError("An active assignment already exists.") from exc
    return assignment


def unassign(
    db: Session, household_id: UUID, assignment_id: UUID, actor: User
) -> CaregiverPatientAssignment:
    _admin_household(db, household_id, actor)
    assignment = db.scalar(
        select(CaregiverPatientAssignment)
        .join(ElderlyPatient)
        .where(
            CaregiverPatientAssignment.assignment_id == assignment_id,
            ElderlyPatient.household_id == household_id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise AccessNotFoundError("Assignment not found.")
    if assignment.unassigned_at is not None:
        raise AccessConflictError("Assignment is already inactive.")
    assignment.unassigned_at = utc_now()
    db.flush()
    return assignment


def issue_access_code(
    db: Session, patient_id: UUID, actor: User, expires_in_hours: int
) -> tuple[PatientAccessCode, str]:
    patient = _patient_for_code_management(db, patient_id, actor)
    now = utc_now()
    existing = db.scalars(
        select(PatientAccessCode)
        .where(
            PatientAccessCode.patient_id == patient.patient_id,
            PatientAccessCode.revoked_at.is_(None),
            PatientAccessCode.used_at.is_(None),
        )
        .with_for_update()
    ).all()
    for code in existing:
        code.revoked_at = now
    # Hash comparison is needed because salted hashes are deliberately not unique.
    # Keep retries bounded even though a 12-character collision is astronomically rare.
    for _ in range(10):
        readable = generate_access_code()
        selector = access_code_selector(readable)
        matching_hashes = db.scalars(
            select(PatientAccessCode.code_hash).where(
                PatientAccessCode.access_code_selector == selector
            )
        ).all()
        if not any(verify_access_code(readable, value) for value in matching_hashes):
            break
    else:
        raise AccessConflictError("Unable to generate a unique patient access code.")
    code = PatientAccessCode(
        patient_id=patient.patient_id,
        code_hash=hash_access_code(readable),
        access_code_selector=selector,
        created_by_user_id=actor.user_id,
        created_at=now,
        expires_at=now + timedelta(hours=expires_in_hours),
    )
    db.add(code)
    db.flush()
    return code, readable


def revoke_access_code(
    db: Session, patient_id: UUID, access_code_id: UUID, actor: User
) -> PatientAccessCode:
    _patient_for_code_management(db, patient_id, actor)
    code = db.scalar(
        select(PatientAccessCode)
        .where(
            PatientAccessCode.access_code_id == access_code_id,
            PatientAccessCode.patient_id == patient_id,
        )
        .with_for_update()
    )
    if code is None:
        raise AccessNotFoundError("Access code not found.")
    if code.revoked_at is None:
        code.revoked_at = utc_now()
        db.flush()
    return code
