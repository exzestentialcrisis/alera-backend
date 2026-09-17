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


def _find_unresolved_inactivity_alert(
    db: Session,
    *,
    patient_id: UUID,
) -> Alert | None:
    return db.scalar(
        select(Alert)
        .where(
            Alert.patient_id == patient_id,
            Alert.condition_key == ConditionKey.INACTIVITY,
            Alert.status.in_(
                (
                    AlertStatus.ACTIVE,
                    AlertStatus.ACKNOWLEDGED,
                )
            ),
        )
        .with_for_update()
    )


def set_inactivity_alert_condition(
    db: Session,
    *,
    patient_id: UUID,
    active: bool,
) -> Alert | None:
    """
    Create, preserve, or automatically resolve an inactivity alert.

    active=True:
        Create one WARNING alert if no unresolved inactivity alert exists.

    active=False:
        Resolve the existing inactivity alert, if one exists.

    The caller owns the database transaction.
    """

    alert = _find_unresolved_inactivity_alert(
        db,
        patient_id=patient_id,
    )

    now = utc_now()

    if active:
        # Do not create duplicate inactivity alerts.
        if alert is not None:
            return alert

        alert = Alert(
            patient_id=patient_id,
            condition_key=ConditionKey.INACTIVITY,
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

    # No existing alert = nothing to resolve.
    if alert is None:
        return None

    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = now
    alert.updated_at = now

    db.flush()

    return alert