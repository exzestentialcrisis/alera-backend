import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.core.config import get_settings
from app.devices.model import CaregiverPushDevice
from app.household_access.model import CaregiverPatientAssignment
from app.notifications.fcm import FCMSender
from app.users.model import AccountStatus, User, UserRole

logger = logging.getLogger(__name__)


def deliver_alert_notifications(bind, alert_ids):
    """Best-effort delivery using a separate transaction after alert commit."""
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
