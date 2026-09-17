import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.devices.model import CaregiverPushDevice
from app.household_access.model import CaregiverPatientAssignment
from app.notifications.fcm import FCMSender
from app.patients.model import ElderlyPatient
from app.reminders.enums import (
    ReminderNotificationChannel,
    ReminderOccurrenceStatus,
)
from app.reminders.model import ReminderOccurrence, ReminderTemplate
from app.users.model import AccountStatus, User, UserRole


logger = logging.getLogger(__name__)


def deliver_missed_reminder_notifications(bind, occurrence_ids) -> None:
    """Best-effort caregiver delivery after the missed transition commits."""
    try:
        sender = FCMSender(get_settings())
        if not sender.configured:
            return
        with Session(bind=bind) as db:
            rows = db.execute(
                select(ReminderOccurrence, ReminderTemplate)
                .join(
                    ReminderTemplate,
                    ReminderTemplate.reminder_template_id
                    == ReminderOccurrence.reminder_template_id,
                )
                .where(
                    ReminderOccurrence.reminder_occurrence_id.in_(occurrence_ids),
                    ReminderOccurrence.status == ReminderOccurrenceStatus.MISSED,
                    ReminderTemplate.notification_channels
                    == ReminderNotificationChannel.PUSH,
                )
            ).all()
            for occurrence, template in rows:
                patient = db.get(ElderlyPatient, template.patient_id)
                patient_user = db.get(User, patient.user_id) if patient else None
                patient_name = (
                    patient_user.full_name.strip()
                    if patient_user and patient_user.full_name
                    else "Your patient"
                )
                title = "Missed reminder"
                body = f"{patient_name} missed {template.title}."
                devices = db.scalars(
                    select(CaregiverPushDevice)
                    .join(User, User.user_id == CaregiverPushDevice.user_id)
                    .join(
                        CaregiverPatientAssignment,
                        CaregiverPatientAssignment.caregiver_user_id == User.user_id,
                    )
                    .where(
                        CaregiverPatientAssignment.patient_id == template.patient_id,
                        CaregiverPatientAssignment.unassigned_at.is_(None),
                        User.account_status == AccountStatus.ACTIVE,
                        User.role.in_([UserRole.CAREGIVER, UserRole.CARE_ADMIN]),
                    )
                    .distinct()
                ).all()
                for device in devices:
                    try:
                        invalid = sender.send_reminder(
                            device.fcm_token,
                            occurrence_id=occurrence.reminder_occurrence_id,
                            template_id=template.reminder_template_id,
                            patient_id=template.patient_id,
                            title=title,
                            body=body,
                        )
                        if invalid:
                            db.execute(
                                delete(CaregiverPushDevice).where(
                                    CaregiverPushDevice.id == device.id,
                                    CaregiverPushDevice.user_id == device.user_id,
                                    CaregiverPushDevice.updated_at == device.updated_at,
                                )
                            )
                    except Exception:
                        logger.warning("Reminder FCM device delivery failed.")
            db.commit()
    except Exception:
        logger.warning("Missed reminder notification delivery unavailable.")
