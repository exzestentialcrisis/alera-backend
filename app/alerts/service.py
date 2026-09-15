from datetime import timedelta
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.alert_actions.model import AlertAction, AlertActionType
from app.alerts.access import accessible_patient_ids
from app.alerts.display import display_mapping
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
from app.notifications.events import queue_alert_notification
from app.patients.model import ElderlyPatient
from app.users.model import User


NORMAL_CONDITIONS = {
    ConditionKey.HR_NORMAL,
    ConditionKey.SPO2_NORMAL,
}
HR_WARNING_CONDITIONS = {ConditionKey.HR_HIGH, ConditionKey.HR_LOW}
HR_WARNING_PERSISTENCE = timedelta(minutes=5)
SPO2_WARNING_MEASUREMENT_COUNT = 2


def alert_display_payload(
    alert: Alert,
    evaluation: EventEvaluation | None = None,
    event: HealthEvent | None = None,
    patient: ElderlyPatient | None = None,
    user: User | None = None,
) -> dict:
    mapping = display_mapping(alert.condition_key)
    return {
        "alert_id": alert.alert_id,
        "patient_id": alert.patient_id,
        "condition_key": alert.condition_key,
        "severity": alert.severity,
        "status": alert.status,
        "detected_at": alert.detected_at,
        "confirmed_at": alert.confirmed_at,
        "resolved_at": alert.resolved_at,
        "created_at": alert.created_at,
        "updated_at": alert.updated_at,
        "patient_display_name": (
            (patient.nickname or user.full_name)
            if patient is not None and user is not None
            else (patient.nickname if patient is not None else None)
        ),
        "metric_type": mapping.metric_type if mapping else None,
        "title": mapping.title if mapping else None,
        "reading_value": event.numeric_value if event else None,
        "reading_unit": mapping.unit if mapping else None,
        "threshold_value": evaluation.threshold_value_used if evaluation else None,
        "threshold_unit": mapping.unit if mapping else None,
        "evaluation_reason": evaluation.evaluation_reason if evaluation else None,
    }


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
    notification_required = alert is None
    is_escalation = False
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
    elif alert.severity != EvaluationSeverity.CRITICAL:
        alert.severity = EvaluationSeverity.CRITICAL
        alert.updated_at = utc_now()
        notification_required = True
        is_escalation = True

    evaluation.alert_id = alert.alert_id
    db.flush()
    if notification_required:
        queue_alert_notification(
            db,
            alert,
            include_acknowledged=is_escalation,
        )
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
        queue_alert_notification(db, alert)

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
        queue_alert_notification(db, alert)

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
    actor: User | None = None,
) -> tuple[
    list[
        tuple[
            Alert,
            EventEvaluation | None,
            HealthEvent | None,
            ElderlyPatient | None,
            User | None,
        ]
    ],
    int,
]:
    filters = []
    if actor is not None:
        filters.append(Alert.patient_id.in_(accessible_patient_ids(actor)))
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
    alerts = list(items)
    if not alerts:
        return [], total
    alert_ids = [alert.alert_id for alert in alerts]
    evaluations = db.scalars(
        select(EventEvaluation)
        .where(EventEvaluation.alert_id.in_(alert_ids))
        .order_by(EventEvaluation.evaluated_at, EventEvaluation.evaluation_id)
    ).all()
    first_evaluation: dict[UUID, EventEvaluation] = {}
    for evaluation in evaluations:
        if evaluation.alert_id is not None:
            first_evaluation.setdefault(evaluation.alert_id, evaluation)
    event_ids = [evaluation.event_id for evaluation in first_evaluation.values()]
    events = (
        {
            event.event_id: event
            for event in db.scalars(
                select(HealthEvent).where(HealthEvent.event_id.in_(event_ids))
            ).all()
        }
        if event_ids
        else {}
    )
    patient_ids = [alert.patient_id for alert in alerts]
    patients = {
        patient.patient_id: patient
        for patient in db.scalars(
            select(ElderlyPatient).where(ElderlyPatient.patient_id.in_(patient_ids))
        ).all()
    }
    user_ids = [patient.user_id for patient in patients.values()]
    users = (
        {
            user.user_id: user
            for user in db.scalars(
                select(User).where(User.user_id.in_(user_ids))
            ).all()
        }
        if user_ids
        else {}
    )
    return [
        (
            alert,
            (evaluation := first_evaluation.get(alert.alert_id)),
            events.get(evaluation.event_id) if evaluation else None,
            (patient := patients.get(alert.patient_id)),
            users.get(patient.user_id) if patient else None,
        )
        for alert in alerts
    ], total


def get_alert_detail(
    db: Session,
    alert_id: UUID,
    actor: User,
) -> tuple[
    Alert,
    EventEvaluation | None,
    HealthEvent | None,
    AlertAction | None,
    ElderlyPatient | None,
    User | None,
]:
    alert = db.scalar(
        select(Alert).where(
            Alert.alert_id == alert_id,
            Alert.patient_id.in_(accessible_patient_ids(actor)),
        )
    )
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
    patient = db.get(ElderlyPatient, alert.patient_id)
    user = db.get(User, patient.user_id) if patient else None
    latest_action = db.scalar(
        select(AlertAction)
        .where(AlertAction.alert_id == alert_id)
        .order_by(
            AlertAction.performed_at.desc(),
            AlertAction.alert_action_id.desc(),
        )
        .limit(1)
    )
    return alert, evaluation, event, latest_action, patient, user


def list_alert_actions(
    db: Session, alert_id: UUID, actor: User
) -> list[AlertAction]:
    if db.scalar(
        select(Alert.alert_id).where(
            Alert.alert_id == alert_id,
            Alert.patient_id.in_(accessible_patient_ids(actor)),
        )
    ) is None:
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


def _lock_alert(db: Session, alert_id: UUID, actor: User) -> Alert:
    alert = db.scalar(
        select(Alert)
        .where(
            Alert.alert_id == alert_id,
            Alert.patient_id.in_(accessible_patient_ids(actor)),
        )
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
    alert = _lock_alert(db, alert_id, actor)
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
    alert = _lock_alert(db, alert_id, actor)
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
    alert = _lock_alert(db, alert_id, actor)
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
    alert = _lock_alert(db, alert_id, actor)
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
    alert = _lock_alert(db, alert_id, actor)
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
