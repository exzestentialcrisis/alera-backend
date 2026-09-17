import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.devices.model import PatientPushDevice
from app.notifications.fcm import FCMSender
from app.nudges.model import PatientNudge, PatientNudgeType
from app.patients.model import ElderlyPatient
from app.users.model import AccountStatus, User, UserRole


logger = logging.getLogger(__name__)
CONTENT = {
    PatientNudgeType.DRINK_WATER: (
        "Time to drink water",
        "Your caregiver sent you a hydration reminder.",
    ),
    PatientNudgeType.TAKE_MEDICATION: (
        "Time to take your medication",
        "Your caregiver sent you a medication reminder.",
    ),
    PatientNudgeType.CHECK_BLOOD_PRESSURE: (
        "Please check your blood pressure",
        "Your caregiver sent you a health check reminder.",
    ),
}


def deliver_patient_nudges(bind, nudge_ids) -> None:
    try:
        sender = FCMSender(get_settings())
        if not sender.configured:
            return
        with Session(bind=bind) as db:
            nudges = db.scalars(
                select(PatientNudge).where(PatientNudge.nudge_id.in_(nudge_ids))
            ).all()
            for nudge in nudges:
                patient = db.get(ElderlyPatient, nudge.patient_id)
                if patient is None or patient.archived_at is not None:
                    continue
                devices = db.scalars(
                    select(PatientPushDevice)
                    .join(User, User.user_id == PatientPushDevice.user_id)
                    .where(
                        PatientPushDevice.user_id == patient.user_id,
                        User.account_status == AccountStatus.ACTIVE,
                        User.role == UserRole.ELDERLY_PATIENT,
                    )
                ).all()
                title, body = CONTENT[nudge.nudge_type]
                for device in devices:
                    try:
                        invalid = sender.send_nudge(
                            device.fcm_token,
                            nudge_id=nudge.nudge_id,
                            patient_id=nudge.patient_id,
                            nudge_type=nudge.nudge_type.value,
                            title=title,
                            body=body,
                        )
                        if invalid:
                            db.execute(
                                delete(PatientPushDevice).where(
                                    PatientPushDevice.id == device.id,
                                    PatientPushDevice.user_id == device.user_id,
                                    PatientPushDevice.updated_at == device.updated_at,
                                )
                            )
                    except Exception:
                        logger.warning("Patient nudge FCM device delivery failed.")
            db.commit()
    except Exception:
        logger.warning("Patient nudge delivery unavailable.")
