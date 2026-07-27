from datetime import timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.alert_actions.model import AlertAction, AlertActionType
from app.alerts.errors import AlertNotFoundError, AlertTransitionConflictError
from app.alerts.model import Alert, AlertStatus
from app.condition_trackers.service import ConditionTrackerUpdateResult
from app.core.time import utc_now
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    EventEvaluation,
)
from app.health_events.model import HealthEvent, ValidationStatus
from app.users.model import User


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


def list_alerts(
    db: Session,
    *,
    patient_id: UUID | None,
    statuses: list[AlertStatus] | None,
    severity: EvaluationSeverity | None,
    condition_key: ConditionKey | None,
    limit: int,
    offset: int,
) -> tuple[list[Alert], int]:
    filters = []
    if patient_id is not None:
        filters.append(Alert.patient_id == patient_id)
    if statuses:
        filters.append(Alert.status.in_(statuses))
    if severity is not None:
        filters.append(Alert.severity == severity)
    if condition_key is not None:
        filters.append(Alert.condition_key == condition_key)

    total = db.scalar(
        select(func.count(Alert.alert_id)).where(*filters)
    ) or 0
    terminal_rank = case(
        (
            Alert.status.in_(
                (
                    AlertStatus.RESOLVED,
                    AlertStatus.FALSE_ALARM,
                    AlertStatus.ARCHIVED,
                )
            ),
            1,
        ),
        else_=0,
    )
    severity_rank = case(
        (Alert.severity == EvaluationSeverity.CRITICAL, 0),
        (Alert.severity == EvaluationSeverity.WARNING, 1),
        else_=2,
    )
    items = db.scalars(
        select(Alert)
        .where(*filters)
        .order_by(
            terminal_rank,
            severity_rank,
            Alert.confirmed_at.desc(),
            Alert.alert_id,
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), total


def get_alert_detail(
    db: Session,
    alert_id: UUID,
) -> tuple[
    Alert,
    EventEvaluation | None,
    HealthEvent | None,
    AlertAction | None,
]:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise AlertNotFoundError("Alert not found.")

    evaluation = db.scalar(
        select(EventEvaluation)
        .where(EventEvaluation.alert_id == alert_id)
        .order_by(
            EventEvaluation.evaluated_at,
            EventEvaluation.evaluation_id,
        )
        .limit(1)
    )
    event = (
        db.get(HealthEvent, evaluation.event_id)
        if evaluation is not None
        else None
    )
    latest_action = db.scalar(
        select(AlertAction)
        .where(AlertAction.alert_id == alert_id)
        .order_by(
            AlertAction.performed_at.desc(),
            AlertAction.alert_action_id.desc(),
        )
        .limit(1)
    )
    return alert, evaluation, event, latest_action


def list_alert_actions(db: Session, alert_id: UUID) -> list[AlertAction]:
    if db.get(Alert, alert_id) is None:
        raise AlertNotFoundError("Alert not found.")
    return list(
        db.scalars(
            select(AlertAction)
            .where(AlertAction.alert_id == alert_id)
            .order_by(
                AlertAction.performed_at,
                AlertAction.alert_action_id,
            )
        ).all()
    )


def _lock_alert(db: Session, alert_id: UUID) -> Alert:
    alert = db.scalar(
        select(Alert)
        .where(Alert.alert_id == alert_id)
        .with_for_update()
    )
    if alert is None:
        raise AlertNotFoundError("Alert not found.")
    return alert


def _reject_archived(alert: Alert) -> None:
    if alert.status == AlertStatus.ARCHIVED:
        raise AlertTransitionConflictError("Archived alerts cannot be changed.")


def _add_action(
    db: Session,
    *,
    alert: Alert,
    actor: User,
    action_type: AlertActionType,
    note: str | None,
    previous_status: AlertStatus,
    new_status: AlertStatus,
    metadata: dict | None = None,
) -> AlertAction:
    action = AlertAction(
        alert_id=alert.alert_id,
        performed_by_user_id=actor.user_id,
        action_type=action_type,
        action_note=note,
        previous_status=previous_status,
        new_status=new_status,
        action_metadata=metadata or {},
    )
    db.add(action)
    db.flush()
    return action


def acknowledge_alert(
    db: Session,
    alert_id: UUID,
    actor: User,
    note: str | None,
) -> tuple[Alert, AlertAction | None, bool]:
    alert = _lock_alert(db, alert_id)
    _reject_archived(alert)
    if alert.status == AlertStatus.ACKNOWLEDGED:
        return alert, None, True
    if alert.status != AlertStatus.ACTIVE:
        raise AlertTransitionConflictError(
            f"Cannot acknowledge an alert in {alert.status.value} status."
        )

    previous = alert.status
    alert.status = AlertStatus.ACKNOWLEDGED
    action = _add_action(
        db,
        alert=alert,
        actor=actor,
        action_type=AlertActionType.ACKNOWLEDGE,
        note=note,
        previous_status=previous,
        new_status=alert.status,
    )
    db.flush()
    return alert, action, False


def resolve_alert(
    db: Session,
    alert_id: UUID,
    actor: User,
    note: str | None,
) -> tuple[Alert, AlertAction | None, bool]:
    alert = _lock_alert(db, alert_id)
    _reject_archived(alert)
    if alert.status == AlertStatus.RESOLVED:
        return alert, None, True
    if alert.status == AlertStatus.FALSE_ALARM:
        raise AlertTransitionConflictError(
            "False-alarm alerts cannot be resolved."
        )

    previous = alert.status
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = utc_now()
    action = _add_action(
        db,
        alert=alert,
        actor=actor,
        action_type=AlertActionType.RESOLVE,
        note=note,
        previous_status=previous,
        new_status=alert.status,
    )
    db.flush()
    return alert, action, False


def mark_false_alarm(
    db: Session,
    alert_id: UUID,
    actor: User,
    reason: str,
) -> tuple[Alert, AlertAction | None, bool]:
    alert = _lock_alert(db, alert_id)
    _reject_archived(alert)
    if alert.status == AlertStatus.FALSE_ALARM:
        return alert, None, True
    if alert.status == AlertStatus.RESOLVED:
        raise AlertTransitionConflictError(
            "Resolved alerts cannot be marked as false alarms."
        )

    previous = alert.status
    alert.status = AlertStatus.FALSE_ALARM
    alert.resolved_at = utc_now()
    action = _add_action(
        db,
        alert=alert,
        actor=actor,
        action_type=AlertActionType.MARK_FALSE_ALARM,
        note=reason,
        previous_status=previous,
        new_status=alert.status,
    )
    db.flush()
    return alert, action, False


def add_alert_note(
    db: Session,
    alert_id: UUID,
    actor: User,
    note: str,
) -> tuple[Alert, AlertAction, bool]:
    alert = _lock_alert(db, alert_id)
    _reject_archived(alert)
    action = _add_action(
        db,
        alert=alert,
        actor=actor,
        action_type=AlertActionType.ADD_NOTE,
        note=note,
        previous_status=alert.status,
        new_status=alert.status,
    )
    return alert, action, False


def log_alert_intervention(
    db: Session,
    alert_id: UUID,
    actor: User,
    intervention_type: str,
    note: str,
) -> tuple[Alert, AlertAction, bool]:
    alert = _lock_alert(db, alert_id)
    _reject_archived(alert)
    action = _add_action(
        db,
        alert=alert,
        actor=actor,
        action_type=AlertActionType.LOG_INTERVENTION,
        note=note,
        previous_status=alert.status,
        new_status=alert.status,
        metadata={"intervention_type": intervention_type},
    )
    return alert, action, False
