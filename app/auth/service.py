from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.errors import AuthenticationError
from app.auth.schema import CaregiverLoginRequest, PatientAccessRequest
from app.auth.security import create_access_token, verify_password
from app.core.config import Settings
from app.core.time import utc_now
from app.household_access.model import PatientAccessCode
from app.household_access.security import verify_access_code
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household, HouseholdStatus
from app.patients.model import ElderlyPatient
from app.users.model import AccountStatus, User, UserRole


def authenticate_caregiver(
    db: Session, credentials: CaregiverLoginRequest, settings: Settings
) -> dict:
    failure = AuthenticationError("Invalid household code, email, or password.")
    household = db.scalar(
        select(Household).where(
            func.upper(Household.household_code)
            == credentials.household_code.strip().upper(),
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
        )
    )
    user = db.scalar(
        select(User).where(func.lower(User.email) == credentials.email.strip().lower())
    )
    if (
        household is None
        or user is None
        or user.account_status is not AccountStatus.ACTIVE
        or user.role not in {UserRole.CAREGIVER, UserRole.CARE_ADMIN}
        or not verify_password(credentials.password, user.password_hash)
    ):
        raise failure

    if user.role is UserRole.CARE_ADMIN:
        authorized = household.created_by_user_id == user.user_id
    else:
        authorized = db.scalar(
            select(CaregiverPatientAssignment.assignment_id)
            .join(
                ElderlyPatient,
                ElderlyPatient.patient_id
                == CaregiverPatientAssignment.patient_id,
            )
            .where(
                CaregiverPatientAssignment.caregiver_user_id == user.user_id,
                CaregiverPatientAssignment.unassigned_at.is_(None),
                ElderlyPatient.household_id == household.household_id,
                ElderlyPatient.archived_at.is_(None),
            )
            .limit(1)
        ) is not None
    if not authorized:
        raise failure

    token, expires_at = create_access_token(
        user_id=user.user_id,
        household_id=household.household_id,
        secret=settings.alera_jwt_secret or "",
        expires_minutes=settings.alera_jwt_access_token_minutes,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "actor": {
            "user_id": user.user_id,
            "full_name": user.full_name,
            "role": user.role,
            "household_id": household.household_id,
            "household_name": household.household_name,
            "household_code": household.household_code,
        },
    }


def authenticate_patient(
    db: Session, credentials: PatientAccessRequest, settings: Settings
) -> dict:

    failure = AuthenticationError("Invalid household code or access code.")
    now = utc_now()
    candidates = db.execute(
        select(PatientAccessCode, ElderlyPatient, User, Household)
        .join(ElderlyPatient, ElderlyPatient.patient_id == PatientAccessCode.patient_id)
        .join(User, User.user_id == ElderlyPatient.user_id)
        .join(Household, Household.household_id == ElderlyPatient.household_id)
        .where(
            func.upper(Household.household_code) == credentials.household_code.strip().upper(),
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
            ElderlyPatient.archived_at.is_(None),
            User.account_status == AccountStatus.ACTIVE,
            User.role == UserRole.ELDERLY_PATIENT,
            PatientAccessCode.used_at.is_(None),
            PatientAccessCode.revoked_at.is_(None),
            PatientAccessCode.expires_at > now,
        )
    )
    for code, patient, user, household in candidates:
        if not verify_access_code(credentials.access_code.strip().upper(), code.code_hash):
            continue
        # Conditional UPDATE is rechecked after concurrent writers commit.
        consumed = db.execute(
            update(PatientAccessCode).where(
                PatientAccessCode.access_code_id == code.access_code_id,
                PatientAccessCode.used_at.is_(None),
                PatientAccessCode.revoked_at.is_(None),
                PatientAccessCode.expires_at > utc_now(),
            ).values(used_at=utc_now()).returning(PatientAccessCode.access_code_id)
        ).scalar_one_or_none()
        if consumed is None:
            raise failure
        token, expires_at = create_access_token(
            user_id=user.user_id,
            household_id=household.household_id,
            secret=settings.alera_jwt_secret or "",
            expires_minutes=settings.alera_jwt_access_token_minutes,
        )
        return {
            "access_token": token, "token_type": "bearer", "expires_at": expires_at,
            "actor": {
                "user_id": user.user_id, "full_name": user.full_name, "role": user.role,
                "household_id": household.household_id,
                "household_name": household.household_name,
                "household_code": household.household_code,
            },
        }
    raise failure
