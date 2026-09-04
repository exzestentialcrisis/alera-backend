import os
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.db.database import get_session_factory
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.patients.model import ElderlyPatient
from app.users.model import AccountStatus, User, UserRole


def seed_demo_caregiver(
    db: Session, *, email: str, password: str, patient_id: UUID
) -> tuple[User, Household]:
    normalized_email = email.strip().lower()
    if not normalized_email or not password:
        raise ValueError("Demo caregiver email and password must not be empty.")
    patient = db.get(ElderlyPatient, patient_id)
    if patient is None:
        raise RuntimeError("Configured demo patient was not found.")
    household = db.get(Household, patient.household_id)
    if household is None:
        raise RuntimeError("The demo patient's household was not found.")
    assigning_admin = db.get(User, household.created_by_user_id)
    if assigning_admin is None or assigning_admin.role is not UserRole.CARE_ADMIN:
        raise RuntimeError("The demo household does not have a valid CARE_ADMIN owner.")

    caregiver = db.scalar(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    if caregiver is None:
        caregiver = User(
            full_name="Demo Caregiver",
            email=normalized_email,
            password_hash=hash_password(password),
            role=UserRole.CAREGIVER,
            account_status=AccountStatus.ACTIVE,
        )
        db.add(caregiver)
        db.flush()
    elif (
        caregiver.role is not UserRole.CAREGIVER
        or caregiver.account_status is not AccountStatus.ACTIVE
    ):
        raise RuntimeError(
            "The configured demo email belongs to a non-active caregiver account."
        )
    elif not verify_password(password, caregiver.password_hash):
        caregiver.password_hash = hash_password(password)

    assignment = db.scalar(
        select(CaregiverPatientAssignment).where(
            CaregiverPatientAssignment.caregiver_user_id == caregiver.user_id,
            CaregiverPatientAssignment.patient_id == patient.patient_id,
            CaregiverPatientAssignment.unassigned_at.is_(None),
        )
    )
    if assignment is None:
        other_household = db.scalar(
            select(ElderlyPatient.household_id)
            .join(
                CaregiverPatientAssignment,
                CaregiverPatientAssignment.patient_id == ElderlyPatient.patient_id,
            )
            .where(
                CaregiverPatientAssignment.caregiver_user_id == caregiver.user_id,
                ElderlyPatient.household_id != household.household_id,
            )
            .limit(1)
        )
        if other_household is not None:
            raise RuntimeError("The demo caregiver belongs to another household.")
        db.add(
            CaregiverPatientAssignment(
                caregiver_user_id=caregiver.user_id,
                patient_id=patient.patient_id,
                assigned_by_user_id=assigning_admin.user_id,
            )
        )
    db.commit()
    return caregiver, household


def _required_environment(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be configured.")
    return value


def main() -> None:
    email = _required_environment("ALERA_DEMO_CAREGIVER_EMAIL")
    password = _required_environment("ALERA_DEMO_CAREGIVER_PASSWORD")
    try:
        patient_id = UUID(_required_environment("ALERA_DEMO_PATIENT_ID"))
    except ValueError as exc:
        raise RuntimeError("ALERA_DEMO_PATIENT_ID must be a valid UUID.") from exc

    with get_session_factory()() as db:
        caregiver, household = seed_demo_caregiver(
            db, email=email, password=password, patient_id=patient_id
        )
    print(f"Caregiver email: {caregiver.email}")
    print(f"Household name: {household.household_name}")
    print(f"Household code: {household.household_code}")


if __name__ == "__main__":
    main()
