from datetime import timedelta

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
HR_WARNING_CONDITIONS = {ConditionKey.HR_HIGH, ConditionKey.HR_LOW}
HR_WARNING_PERSISTENCE = timedelta(minutes=5)
SPO2_WARNING_MEASUREMENT_COUNT = 2


def _find_unresolved_alert(
    db: Session,
    event: HealthEvent,
    evaluation: EventEvaluation,
) -> Alert | None:
    return db.scalar(
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

    alert = _find_unresolved_alert(db, event, evaluation)
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


def process_persistent_hr_warning(
    db: Session,
    event: HealthEvent,
    evaluation: EventEvaluation,
    tracker_result: ConditionTrackerUpdateResult,
) -> Alert | None:
    """Create or reuse a persisted HR Warning without ending the transaction."""
    tracker = tracker_result.tracker
    if (
        event.validation_status != ValidationStatus.VALID_REALTIME
        or evaluation.severity != EvaluationSeverity.WARNING
        or evaluation.condition_key not in HR_WARNING_CONDITIONS
        or not tracker_result.applied
        or tracker is None
        or not tracker.active
        or tracker.last_event_id != event.event_id
    ):
        return None

    if event.recorded_at - tracker.started_at < HR_WARNING_PERSISTENCE:
        return None

    evaluation.persistence_met = True
    alert = _find_unresolved_alert(db, event, evaluation)
    if alert is None:
        # A terminal alert with the same detected time is durable memory that
        # this serialized tracker occurrence already produced a Warning.
        alert = db.scalar(
            select(Alert)
            .where(
                Alert.patient_id == event.patient_id,
                Alert.condition_key == evaluation.condition_key,
                Alert.detected_at == tracker.started_at,
            )
            .order_by(Alert.created_at.desc())
            .with_for_update()
        )

    if alert is None:
        alert = Alert(
            patient_id=event.patient_id,
            condition_key=evaluation.condition_key,
            severity=EvaluationSeverity.WARNING,
            status=AlertStatus.ACTIVE,
            detected_at=tracker.started_at,
            confirmed_at=event.recorded_at,
            resolved_at=None,
        )
        db.add(alert)
        db.flush()

    # Do not downgrade Critical severity or change caregiver-handling status.
    evaluation.alert_id = alert.alert_id
    db.flush()
    return alert


def process_consecutive_spo2_warning(
    db: Session,
    event: HealthEvent,
    evaluation: EventEvaluation,
    tracker_result: ConditionTrackerUpdateResult,
) -> Alert | None:
    """Create or reuse a SpO₂ Warning after two accepted candidates."""
    tracker = tracker_result.tracker
    if (
        event.validation_status != ValidationStatus.VALID_REALTIME
        or evaluation.severity != EvaluationSeverity.WARNING
        or evaluation.condition_key != ConditionKey.SPO2_LOW
        or not tracker_result.applied
        or tracker is None
        or not tracker.active
        or tracker.last_event_id != event.event_id
    ):
        return None

    alert = _find_unresolved_alert(db, event, evaluation)
    if (
        tracker.consecutive_event_count < SPO2_WARNING_MEASUREMENT_COUNT
        and (
            alert is None
            or alert.severity != EvaluationSeverity.CRITICAL
        )
    ):
        return None

    evaluation.persistence_met = True
    if alert is None:
        alert = db.scalar(
            select(Alert)
            .where(
                Alert.patient_id == event.patient_id,
                Alert.condition_key == evaluation.condition_key,
                Alert.detected_at == tracker.started_at,
            )
            .order_by(Alert.created_at.desc())
            .with_for_update()
        )

    if alert is None:
        alert = Alert(
            patient_id=event.patient_id,
            condition_key=evaluation.condition_key,
            severity=EvaluationSeverity.WARNING,
            status=AlertStatus.ACTIVE,
            detected_at=tracker.started_at,
            confirmed_at=event.recorded_at,
            resolved_at=None,
        )
        db.add(alert)
        db.flush()

    # Existing Critical severity and all caregiver-handling states are kept.
    evaluation.alert_id = alert.alert_id
    db.flush()
    return alert
