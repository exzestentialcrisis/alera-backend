"""Best-effort push delivery for committed help-request changes."""

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.devices.model import CaregiverPushDevice, PatientPushDevice
from app.help_requests.model import HelpRequest, HelpRequestStatus
from app.household_access.model import CaregiverPatientAssignment
from app.notifications.fcm import FCMSender
from app.patients.model import ElderlyPatient
from app.users.model import AccountStatus, User, UserRole

logger = logging.getLogger(__name__)

EXPECTED_STATUS = {
    "CREATED": HelpRequestStatus.PENDING,
    "ACKNOWLEDGED": HelpRequestStatus.ACKNOWLEDGED,
    "RESOLVED": HelpRequestStatus.RESOLVED,
}


def _patient_name(patient: ElderlyPatient, user: User) -> str:
    return (
        (patient.nickname or "").strip()
        or (user.full_name or "").strip()
        or "Your patient"
    )


def _content(event_name: str, patient_name: str) -> tuple[str, str]:
    if event_name == "CREATED":
        return (
            "Help requested",
            f"{patient_name} requested assistance.",
        )
    if event_name == "ACKNOWLEDGED":
        return (
            "Help request acknowledged",
            "Your caregiver knows that you need assistance.",
        )
    return (
        "Help request resolved",
        "Your caregiver marked your request as resolved.",
    )


def _delete_invalid_device(db: Session, model, device) -> None:
    db.execute(
        delete(model).where(
            model.id == device.id,
            model.user_id == device.user_id,
            model.updated_at == device.updated_at,
        )
    )


def _caregiver_devices(db: Session, patient_id):
    return db.scalars(
        select(CaregiverPushDevice)
        .join(User, User.user_id == CaregiverPushDevice.user_id)
        .join(
            CaregiverPatientAssignment,
            CaregiverPatientAssignment.caregiver_user_id == User.user_id,
        )
        .where(
            CaregiverPatientAssignment.patient_id == patient_id,
            CaregiverPatientAssignment.unassigned_at.is_(None),
            User.account_status == AccountStatus.ACTIVE,
            User.role.in_([UserRole.CAREGIVER, UserRole.CARE_ADMIN]),
        )
        .distinct()
    ).all()


def _patient_devices(db: Session, patient_user: User):
    if (
        patient_user.role is not UserRole.ELDERLY_PATIENT
        or patient_user.account_status is not AccountStatus.ACTIVE
    ):
        return []

    return db.scalars(
        select(PatientPushDevice).where(
            PatientPushDevice.user_id == patient_user.user_id
        )
    ).all()


def deliver_help_request_notifications(bind, intents) -> None:
    """Deliver using a separate transaction after the request commits."""
    try:
        sender = FCMSender(get_settings())
        if not sender.configured:
            return

        with Session(bind=bind) as db:
            for help_request_id, event_name in intents:
                expected_status = EXPECTED_STATUS.get(event_name)
                if expected_status is None:
                    continue

                request = db.get(HelpRequest, help_request_id)
                if request is None or request.status is not expected_status:
                    continue

                patient = db.get(
                    ElderlyPatient,
                    request.patient_id,
                )
                if patient is None or patient.archived_at is not None:
                    continue

                patient_user = db.get(User, patient.user_id)
                if patient_user is None:
                    continue

                patient_name = _patient_name(patient, patient_user)
                title, body = _content(event_name, patient_name)

                if event_name == "CREATED":
                    model = CaregiverPushDevice
                    devices = _caregiver_devices(
                        db,
                        request.patient_id,
                    )
                else:
                    model = PatientPushDevice
                    devices = _patient_devices(db, patient_user)

                for device in devices:
                    try:
                        invalid = sender.send_help_request(
                            device.fcm_token,
                            help_request_id=request.help_request_id,
                            patient_id=request.patient_id,
                            event_name=event_name,
                            request_status=request.status.value,
                            patient_display_name=patient_name,
                            title=title,
                            body=body,
                        )
                        if invalid:
                            _delete_invalid_device(
                                db,
                                model,
                                device,
                            )
                    except Exception:
                        logger.warning("Help-request FCM device delivery failed.")

            db.commit()
    except Exception:
        logger.warning("Help-request notification delivery unavailable.")
