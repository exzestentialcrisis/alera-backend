import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.core.config import get_settings
from app.devices.model import CaregiverPushDevice
from app.event_evaluations.model import EventEvaluation, EvaluationSeverity
from app.health_events.model import HealthEvent
from app.patients.model import ElderlyPatient
from app.notifications.content import notification_content
from app.household_access.model import CaregiverPatientAssignment
from app.notifications.fcm import FCMSender
from app.notifications.sms import SemaphoreSMSSender
from app.patients.photo_storage import public_profile_photo_url
from app.users.model import AccountStatus, User, UserRole

logger = logging.getLogger(__name__)


def deliver_alert_notifications(bind, notification_intents):
    """Best-effort delivery using a separate transaction after alert commit."""
    # Deferred import: alert services register the post-commit notification hook.
    from app.alerts.service import alert_display_payload

    try:
        settings = get_settings()
        push_sender = FCMSender(settings)
        sms_sender = SemaphoreSMSSender(settings)
        if not push_sender.configured and not sms_sender.configured:
            return
        include_acknowledged = {}
        for alert_id, allow_acknowledged in notification_intents:
            include_acknowledged[alert_id] = (
                include_acknowledged.get(alert_id, False) or allow_acknowledged
            )
        with Session(bind=bind) as db:
            alerts = db.scalars(
                select(Alert).where(Alert.alert_id.in_(include_acknowledged))
            ).all()
            for alert in alerts:
                if alert.status is not AlertStatus.ACTIVE and not (
                    alert.status is AlertStatus.ACKNOWLEDGED
                    and include_acknowledged[alert.alert_id]
                ):
                    continue
                # Use the evaluation that queued this delivery. New alerts have one
                # linked evaluation; escalations need the latest Critical reading.
                evaluation = db.scalar(
                    select(EventEvaluation)
                    .where(EventEvaluation.alert_id == alert.alert_id)
                    .order_by(
                        EventEvaluation.evaluated_at.desc(),
                        EventEvaluation.evaluation_id.desc(),
                    )
                    .limit(1)
                )
                event = db.get(HealthEvent, evaluation.event_id) if evaluation else None
                patient = db.get(ElderlyPatient, alert.patient_id)
                user = db.get(User, patient.user_id) if patient else None
                display_payload = alert_display_payload(
                    alert,
                    evaluation,
                    event,
                    patient,
                    user,
                )
                title, body = notification_content(display_payload)
                metric_type = display_payload.get("metric_type")
                devices = (
                    db.scalars(
                        select(CaregiverPushDevice)
                        .join(User, User.user_id == CaregiverPushDevice.user_id)
                        .join(
                            CaregiverPatientAssignment,
                            CaregiverPatientAssignment.caregiver_user_id
                            == User.user_id,
                        )
                        .where(
                            CaregiverPatientAssignment.patient_id == alert.patient_id,
                            CaregiverPatientAssignment.unassigned_at.is_(None),
                            User.account_status == AccountStatus.ACTIVE,
                            User.role.in_([UserRole.CAREGIVER, UserRole.CARE_ADMIN]),
                        )
                        .distinct()
                    ).all()
                    if push_sender.configured
                    else []
                )
                for device in devices:
                    try:
                        patient_photo_url = (
                            public_profile_photo_url(
                                settings=settings,
                                object_path=patient.profile_photo_path,
                            )
                            if patient is not None
                            else None
                        )
                        invalid = push_sender.send(
                            device.fcm_token,
                            alert_id=alert.alert_id,
                            patient_id=alert.patient_id,
                            patient_display_name=(
                                display_payload.get("patient_display_name") or ""
                            ),
                            patient_photo_url=patient_photo_url or "",
                            metric_type=(
                                metric_type.value if metric_type is not None else ""
                            ),
                            title=title,
                            body=body,
                        )
                        if invalid:
                            # Do not delete a registration refreshed/reassigned during delivery.
                            db.execute(
                                delete(CaregiverPushDevice).where(
                                    CaregiverPushDevice.id == device.id,
                                    CaregiverPushDevice.user_id == device.user_id,
                                    CaregiverPushDevice.updated_at == device.updated_at,
                                )
                            )
                    except Exception:
                        logger.warning("FCM device delivery failed.")

                if (
                    sms_sender.configured
                    and evaluation is not None
                    and evaluation.severity is EvaluationSeverity.CRITICAL
                ):
                    backup = db.scalar(
                        select(User)
                        .join(
                            CaregiverPatientAssignment,
                            CaregiverPatientAssignment.caregiver_user_id
                            == User.user_id,
                        )
                        .where(
                            CaregiverPatientAssignment.patient_id == alert.patient_id,
                            CaregiverPatientAssignment.assignment_role == "BACKUP",
                            CaregiverPatientAssignment.unassigned_at.is_(None),
                            User.account_status == AccountStatus.ACTIVE,
                            User.role == UserRole.CAREGIVER,
                            User.phone_number.is_not(None),
                        )
                    )
                    if backup is not None and backup.phone_number:
                        try:
                            sms_sender.send(
                                backup.phone_number,
                                f"ALERA CRITICAL: {title}. {body}",
                            )
                        except Exception:
                            logger.warning("Backup caregiver SMS delivery failed.")
            db.commit()
    except Exception:
        logger.warning("Alert notification delivery unavailable.")
