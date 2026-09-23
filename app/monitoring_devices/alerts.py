from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.core.time import utc_now
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
)
from app.notifications.events import queue_alert_notification


DEVICE_ALERT_CONDITIONS = {
    ConditionKey.PHONE_DISCONNECTED,
    ConditionKey.WATCH_DISCONNECTED,
    ConditionKey.WATCH_NOT_WORN,
    ConditionKey.PHONE_BATTERY_LOW,
    ConditionKey.WATCH_BATTERY_LOW,
    ConditionKey.PATIENT_LOGGED_OUT,
}


def _find_unresolved_device_alert(
    db: Session,
    *,
    patient_id: UUID,
    condition_key: ConditionKey,
) -> Alert | None:
    return db.scalar(
        select(Alert)
        .where(
            Alert.patient_id == patient_id,
            Alert.condition_key == condition_key,
            Alert.status.in_(
                (
                    AlertStatus.ACTIVE,
                    AlertStatus.ACKNOWLEDGED,
                )
            ),
        )
        .with_for_update()
     )


def set_device_alert_condition(
    db: Session,
    *,
    patient_id: UUID,
    condition_key: ConditionKey,
    active: bool,
) -> Alert | None:
    """
    Create, preserve, or automatically resolve a device alert.

    active=True:
        Create an ACTIVE alert if one does not already exist.

    active=False:
        Resolve the existing ACTIVE/ACKNOWLEDGED alert, if any.

    This function does not commit. The caller owns the transaction.
    """

    if condition_key not in DEVICE_ALERT_CONDITIONS:
        raise ValueError(
            f"{condition_key.value} is not a device alert condition."
        )

    alert = _find_unresolved_device_alert(
        db,
        patient_id=patient_id,
        condition_key=condition_key,
    )

    now = utc_now()

    if active:
        if alert is not None:
            return alert

        alert = Alert(
            patient_id=patient_id,
            condition_key=condition_key,
            severity=EvaluationSeverity.WARNING,
            status=AlertStatus.ACTIVE,
            detected_at=now,
            confirmed_at=now,
            resolved_at=None,
        )

        db.add(alert)
        db.flush()

        queue_alert_notification(
            db,
            alert,
        )

        return alert

    if alert is None:
        return None

    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = now
    alert.updated_at = now

    db.flush()

    return alert