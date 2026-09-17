from uuid import UUID

from app.auth.security import create_access_token
from app.core.config import get_settings
from app.db.database import get_session_factory
from app.households.model import Household, HouseholdStatus
from app.patients.model import (
    ElderlyPatient,
    HealthPlatform,
    IntegrationStatus,
)
from app.users.model import (
    AccountStatus,
    User,
    UserRole,
)


ADMIN_USER_ID = UUID(
    "11111111-1111-1111-1111-111111111111"
)

PATIENT_USER_ID = UUID(
    "22222222-2222-2222-2222-222222222222"
)

HOUSEHOLD_ID = UUID(
    "33333333-3333-3333-3333-333333333334"
)

PATIENT_ID = UUID(
    "44444444-4444-4444-4444-444444444444"
)


def main() -> None:
    settings = get_settings()

    if not settings.alera_jwt_secret:
        raise RuntimeError(
            "ALERA_JWT_SECRET must be configured in .env."
        )

    with get_session_factory()() as db:
        admin = db.get(
            User,
            ADMIN_USER_ID,
        )

        if admin is None:
            admin = User(
                user_id=ADMIN_USER_ID,
                full_name="Local Test Admin",
                email="local-admin@alera.test",
                role=UserRole.CARE_ADMIN,
                account_status=AccountStatus.ACTIVE,
            )

            db.add(admin)
            db.flush()

        household = db.get(
            Household,
            HOUSEHOLD_ID,
        )

        if household is None:
            household = Household(
                household_id=HOUSEHOLD_ID,
                created_by_user_id=ADMIN_USER_ID,
                household_name="Local Test Household",
                household_code="TEST-0001",
                household_status=HouseholdStatus.ACTIVE,
            )

            db.add(household)
            db.flush()

        patient_user = db.get(
            User,
            PATIENT_USER_ID,
        )

        if patient_user is None:
            patient_user = User(
                user_id=PATIENT_USER_ID,
                full_name="Local Activity Patient",
                role=UserRole.ELDERLY_PATIENT,
                account_status=AccountStatus.ACTIVE,
            )

            db.add(patient_user)
            db.flush()

        patient = db.get(
            ElderlyPatient,
            PATIENT_ID,
        )

        if patient is None:
            patient = ElderlyPatient(
                patient_id=PATIENT_ID,
                user_id=PATIENT_USER_ID,
                household_id=HOUSEHOLD_ID,
                nickname="Local Activity Patient",
                normal_hr_min=60,
                normal_hr_max=100,
                usual_spo2_min=95,
                health_platform=HealthPlatform.HEALTH_CONNECT,
                integration_status=IntegrationStatus.CONNECTED,
            )

            db.add(patient)

        db.commit()

        token, expires_at = create_access_token(
            user_id=PATIENT_USER_ID,
            household_id=HOUSEHOLD_ID,
            secret=settings.alera_jwt_secret,
            expires_minutes=60,
        )

        print()
        print("LOCAL ACTIVITY TEST READY")
        print()
        print(f"Patient ID: {PATIENT_ID}")
        print(f"Household ID: {HOUSEHOLD_ID}")
        print(f"Token expires: {expires_at}")
        print()
        print("Bearer token:")
        print(token)


if __name__ == "__main__":
    main()