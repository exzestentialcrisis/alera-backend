import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.core.config import get_settings
from app.devices.model import CaregiverPushDevice
from app.event_evaluations.model import EventEvaluation
from app.health_events.model import HealthEvent
from app.patients.model import ElderlyPatient
from app.notifications.content import notification_content
from app.household_access.model import CaregiverPatientAssignment
from app.notifications.fcm import FCMSender
from app.users.model import AccountStatus, User, UserRole

logger = logging.getLogger(__name__)


def deliver_alert_notifications(bind, alert_ids):
    """Best-effort delivery using a separate transaction after alert commit."""
    # Deferred import: alert services register the post-commit notification hook.
    from app.alerts.service import alert_display_payload

    try:
        sender = FCMSender(get_settings())
        if not sender.configured:
            return
        with Session(bind=bind) as db:
            alerts = db.scalars(
                select(Alert).where(
                    Alert.alert_id.in_(alert_ids), Alert.status == AlertStatus.ACTIVE
                )
            ).all()
            for alert in alerts:
                # Match the enriched API's first triggering evaluation selection.
                evaluation = db.scalar(
                    select(EventEvaluation)
                    .where(EventEvaluation.alert_id == alert.alert_id)
                    .order_by(EventEvaluation.evaluated_at, EventEvaluation.evaluation_id)
                    .limit(1)
                )
                event = db.get(HealthEvent, evaluation.event_id) if evaluation else None
                patient = db.get(ElderlyPatient, alert.patient_id)
                user = db.get(User, patient.user_id) if patient else None
                title, body = notification_content(
                    alert_display_payload(alert, evaluation, event, patient, user)
                )
                devices = db.scalars(
                    select(CaregiverPushDevice)
                    .join(User, User.user_id == CaregiverPushDevice.user_id)
                    .join(
                        CaregiverPatientAssignment,
                        CaregiverPatientAssignment.caregiver_user_id == User.user_id,
                    )
                    .where(
                        CaregiverPatientAssignment.patient_id == alert.patient_id,
                        CaregiverPatientAssignment.unassigned_at.is_(None),
                        User.account_status == AccountStatus.ACTIVE,
                        User.role.in_([UserRole.CAREGIVER, UserRole.CARE_ADMIN]),
                    )
                    .distinct()
                ).all()
                for device in devices:
                    try:
                        invalid = sender.send(
                            device.fcm_token,
                            alert_id=alert.alert_id,
                            patient_id=alert.patient_id,
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
            db.commit()
    except Exception:
        logger.warning("Alert notification delivery unavailable.")
