from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.errors import AuthenticationError
from app.auth.schema import (
    CaregiverLoginRequest,
    HouseholdValidationRequest,
    PatientAccessRequest,
)
from app.auth.security import create_access_token, verify_password
from app.core.config import Settings
from app.core.time import utc_now
from app.household_access.model import CaregiverPatientAssignment, PatientAccessCode
from app.household_access.security import (
    access_code_selector,
    normalize_access_code,
    verify_access_code,
)
from app.monitoring_devices.model import (
    DeviceConnectionStatus,
    MonitoringDevice,
    MonitoringDeviceType,
)
from app.households.codes import normalize_household_code
from app.households.model import Household, HouseholdStatus
from app.patients.model import ElderlyPatient
from app.users.model import AccountStatus, User, UserRole
from app.monitoring_devices.alerts import set_device_alert_condition
from app.event_evaluations.model import ConditionKey


def authenticate_caregiver(
    db: Session, credentials: CaregiverLoginRequest, settings: Settings
) -> dict:
    failure = AuthenticationError("Invalid household code, email, or password.")
    normalized_household_code = normalize_household_code(credentials.household_code)
    if normalized_household_code is None:
        raise failure
    household = db.scalar(
        select(Household).where(
            func.upper(Household.household_code) == normalized_household_code,
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


def validate_household_code(
    db: Session, payload: HouseholdValidationRequest
) -> dict | None:
    """Resolve only the public display data for an available household."""
    normalized = normalize_household_code(payload.household_code)
    if normalized is None:
        return None
    household_name = db.scalar(
        select(Household.household_name).where(
            func.upper(Household.household_code) == normalized,
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
        )
    )
    if household_name is None:
        return None
    return {"valid": True, "household_name": household_name}


def authenticate_patient(
    db: Session, credentials: PatientAccessRequest, settings: Settings
) -> dict:

    failure = AuthenticationError("Invalid access code.")
    normalized = normalize_access_code(credentials.access_code)
    if normalized is None:
        raise failure
    now = utc_now()
    # A short fixed bound prevents selector collisions from amplifying scrypt work.
    candidates = db.execute(
        select(PatientAccessCode, ElderlyPatient, User, Household)
        .join(ElderlyPatient, ElderlyPatient.patient_id == PatientAccessCode.patient_id)
        .join(User, User.user_id == ElderlyPatient.user_id)
        .join(Household, Household.household_id == ElderlyPatient.household_id)
        .where(
            PatientAccessCode.access_code_selector == access_code_selector(normalized),
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
            ElderlyPatient.archived_at.is_(None),
            User.account_status == AccountStatus.ACTIVE,
            User.role == UserRole.ELDERLY_PATIENT,
            PatientAccessCode.used_at.is_(None),
            PatientAccessCode.revoked_at.is_(None),
            PatientAccessCode.expires_at > now,
        ).limit(8)
    )
    for code, patient, user, household in candidates:
        if not verify_access_code(normalized, code.code_hash):
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
        phone = db.scalar(
            select(MonitoringDevice)
            .where(
                MonitoringDevice.patient_id == patient.patient_id,
                MonitoringDevice.device_type
                == MonitoringDeviceType.PHONE,
            )
            .with_for_update()
        )

        if phone is not None:
            now = utc_now()

            if (
                phone.connection_status
                is not DeviceConnectionStatus.CONNECTED
            ):
                phone.connection_status = (
                    DeviceConnectionStatus.CONNECTED
                )
                phone.status_changed_at = now

            phone.reported_at = now
            phone.last_seen_at = now
            phone.updated_at = now

            set_device_alert_condition(
                db,
                patient_id=patient.patient_id,
                condition_key=ConditionKey.PATIENT_LOGGED_OUT,
                active=False,
            )

            set_device_alert_condition(
                db,
                patient_id=patient.patient_id,
                condition_key=ConditionKey.PHONE_DISCONNECTED,
                active=False,
            )

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
