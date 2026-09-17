from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household, HouseholdStatus
from app.nudges.events import queue_patient_nudge
from app.nudges.model import PatientNudge, PatientNudgeType
from app.patients.model import ElderlyPatient
from app.users.model import User, UserRole


class NudgeForbiddenError(Exception):
    pass


class NudgeNotFoundError(Exception):
    pass


class NudgeConflictError(Exception):
    pass


def create_patient_nudge(
    db: Session,
    *,
    actor: User,
    patient_id: UUID,
    nudge_type: PatientNudgeType,
    client_action_id: UUID,
) -> tuple[PatientNudge, bool]:
    if actor.role is not UserRole.CAREGIVER:
        raise NudgeForbiddenError("Only assigned caregivers may send patient nudges.")

    patient = db.scalar(
        select(ElderlyPatient)
        .join(Household, Household.household_id == ElderlyPatient.household_id)
        .join(
            CaregiverPatientAssignment,
            CaregiverPatientAssignment.patient_id == ElderlyPatient.patient_id,
        )
        .where(
            ElderlyPatient.patient_id == patient_id,
            ElderlyPatient.archived_at.is_(None),
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
            CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
            CaregiverPatientAssignment.unassigned_at.is_(None),
        )
    )
    if patient is None:
        raise NudgeNotFoundError("Patient not found.")

    existing = db.scalar(
        select(PatientNudge).where(PatientNudge.client_action_id == client_action_id)
    )
    if existing is not None:
        if (
            existing.patient_id != patient_id
            or existing.sent_by_user_id != actor.user_id
            or existing.nudge_type is not nudge_type
        ):
            raise NudgeConflictError("client_action_id was already used for another nudge.")
        return existing, True

    nudge = PatientNudge(
        patient_id=patient_id,
        sent_by_user_id=actor.user_id,
        nudge_type=nudge_type,
        client_action_id=client_action_id,
    )
    savepoint = db.begin_nested()
    try:
        db.add(nudge)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        existing = db.scalar(
            select(PatientNudge).where(
                PatientNudge.client_action_id == client_action_id
            )
        )
        if existing is None:
            raise
        if (
            existing.patient_id != patient_id
            or existing.sent_by_user_id != actor.user_id
            or existing.nudge_type is not nudge_type
        ):
            raise NudgeConflictError(
                "client_action_id was already used for another nudge."
            )
        return existing, True
    queue_patient_nudge(db, nudge.nudge_id)
    return nudge, False
