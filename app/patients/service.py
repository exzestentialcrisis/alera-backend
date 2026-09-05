from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.household_access.errors import AccessForbiddenError
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household, HouseholdStatus
from app.patients.model import ElderlyPatient
from app.patients.schema import PatientCreate, PatientCreated
from app.users.model import AccountStatus, User, UserRole


def create_patient(db: Session, actor: User, household_id: UUID,
                   payload: PatientCreate) -> PatientCreated:
    household = db.get(Household, household_id)
    permitted = False
    if (actor.account_status is AccountStatus.ACTIVE and household is not None
            and household.household_status is HouseholdStatus.ACTIVE
            and household.archived_at is None):
        if actor.role is UserRole.CARE_ADMIN:
            permitted = household.created_by_user_id == actor.user_id
        elif actor.role is UserRole.CAREGIVER:
            permitted = db.scalar(
                select(CaregiverPatientAssignment.assignment_id)
                .join(ElderlyPatient, ElderlyPatient.patient_id == CaregiverPatientAssignment.patient_id)
                .where(
                    CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
                    CaregiverPatientAssignment.unassigned_at.is_(None),
                    ElderlyPatient.household_id == household_id,
                    ElderlyPatient.archived_at.is_(None),
                ).limit(1)
            ) is not None
    if not permitted:
        raise AccessForbiddenError("Actor is not permitted to create patients in this household.")

    user = User(full_name=payload.full_name, phone_number=payload.phone_number,
                role=UserRole.ELDERLY_PATIENT)
    db.add(user)
    db.flush()
    fields = payload.model_dump(exclude={"full_name", "phone_number", "monitoring_notes"})
    patient = ElderlyPatient(
        user_id=user.user_id, household_id=household_id,
        health_notes=payload.monitoring_notes, **fields,
    )
    db.add(patient)
    db.flush()
    assignment = None
    if actor.role is UserRole.CAREGIVER:
        assignment = CaregiverPatientAssignment(
            caregiver_user_id=actor.user_id, patient_id=patient.patient_id,
            assigned_by_user_id=actor.user_id,
        )
        db.add(assignment)
        db.flush()
    return PatientCreated(
        **payload.model_dump(), patient_id=patient.patient_id, user_id=user.user_id,
        household_id=household_id, account_status=user.account_status,
        archived_at=patient.archived_at, assignment=assignment, created_at=patient.created_at,
    )
