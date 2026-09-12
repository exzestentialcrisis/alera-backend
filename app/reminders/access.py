from uuid import UUID

from sqlalchemy import Select, false, select
from sqlalchemy.orm import Session

from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household, HouseholdStatus
from app.patients.model import ElderlyPatient
from app.reminders.errors import (
    ReminderAccessForbiddenError,
    ReminderNotFoundError,
    ReminderQueryValidationError,
)
from app.users.model import User, UserRole


def visible_patient_ids(actor: User) -> Select:
    """Return active, non-archived patients visible to the actor."""
    statement = (
        select(ElderlyPatient.patient_id)
        .join(Household, Household.household_id == ElderlyPatient.household_id)
        .where(
            ElderlyPatient.archived_at.is_(None),
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
        )
    )
    if actor.role is UserRole.ELDERLY_PATIENT:
        return statement.where(ElderlyPatient.user_id == actor.user_id)
    if actor.role is UserRole.CAREGIVER:
        return statement.join(
            CaregiverPatientAssignment,
            CaregiverPatientAssignment.patient_id == ElderlyPatient.patient_id,
        ).where(
            CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
            CaregiverPatientAssignment.unassigned_at.is_(None),
        )
    if actor.role is UserRole.CARE_ADMIN:
        return statement.where(Household.created_by_user_id == actor.user_id)
    return statement.where(false())


def resolve_list_patient_id(
    db: Session,
    *,
    actor: User,
    patient_id: UUID | None,
) -> UUID:
    """Resolve a list request's patient without disclosing caregiver/admin scope."""
    if actor.role is UserRole.ELDERLY_PATIENT:
        own_patient_id = db.scalar(visible_patient_ids(actor))
        if own_patient_id is None:
            raise ReminderNotFoundError("Reminder patient not found.")
        if patient_id is not None and patient_id != own_patient_id:
            raise ReminderAccessForbiddenError(
                "Patients may only read their own reminders."
            )
        return own_patient_id

    if actor.role not in {UserRole.CAREGIVER, UserRole.CARE_ADMIN}:
        raise ReminderAccessForbiddenError("Actor is not permitted to read reminders.")
    if patient_id is None:
        raise ReminderQueryValidationError("patient_id is required for caregiver access.")
    if db.scalar(
        select(ElderlyPatient.patient_id).where(
            ElderlyPatient.patient_id == patient_id,
            ElderlyPatient.patient_id.in_(visible_patient_ids(actor)),
        )
    ) is None:
        raise ReminderNotFoundError("Reminder patient not found.")
    return patient_id
