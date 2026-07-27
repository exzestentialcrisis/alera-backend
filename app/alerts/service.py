from sqlalchemy import select
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.condition_trackers.service import ConditionTrackerUpdateResult
from app.core.time import utc_now
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    EventEvaluation,
)
from app.health_events.model import HealthEvent, ValidationStatus


NORMAL_CONDITIONS = {
    ConditionKey.HR_NORMAL,
    ConditionKey.SPO2_NORMAL,
}


def process_immediate_critical_alert(
    db: Session,
    event: HealthEvent,
    evaluation: EventEvaluation,
    tracker_result: ConditionTrackerUpdateResult,
) -> Alert | None:
    """Create or reuse an immediate Critical alert without ending the transaction."""
    tracker = tracker_result.tracker
    if (
        event.validation_status != ValidationStatus.VALID_REALTIME
        or evaluation.severity != EvaluationSeverity.CRITICAL
        or evaluation.condition_key in NORMAL_CONDITIONS
        or not tracker_result.applied
        or tracker is None
        or not tracker.active
        or tracker.last_event_id != event.event_id
    ):
        return None

    alert = db.scalar(
        select(Alert)
        .where(
            Alert.patient_id == event.patient_id,
            Alert.condition_key == evaluation.condition_key,
            Alert.status.in_(
                (AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED)
            ),
        )
        .with_for_update()
    )
    if alert is None:
        alert = Alert(
            patient_id=event.patient_id,
            condition_key=evaluation.condition_key,
            severity=EvaluationSeverity.CRITICAL,
            status=AlertStatus.ACTIVE,
            detected_at=tracker.started_at,
            confirmed_at=event.recorded_at,
            resolved_at=None,
        )
        db.add(alert)
        db.flush()
    else:
        alert.severity = EvaluationSeverity.CRITICAL
        alert.updated_at = utc_now()

    evaluation.alert_id = alert.alert_id
    db.flush()
    return alert
