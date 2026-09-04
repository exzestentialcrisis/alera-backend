from sqlalchemy import Select, select

from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.patients.model import ElderlyPatient
from app.users.model import User, UserRole


def accessible_patient_ids(actor: User) -> Select:
    """Return the patient-id scope visible to a caregiver-facing actor."""
    if actor.role is UserRole.CAREGIVER:
        return select(CaregiverPatientAssignment.patient_id).where(
            CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
            CaregiverPatientAssignment.unassigned_at.is_(None),
        )
    if actor.role is UserRole.CARE_ADMIN:
        return (
            select(ElderlyPatient.patient_id)
            .join(Household, Household.household_id == ElderlyPatient.household_id)
            .where(Household.created_by_user_id == actor.user_id)
        )
    return select(ElderlyPatient.patient_id).where(False)
