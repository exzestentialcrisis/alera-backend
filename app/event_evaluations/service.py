from decimal import Decimal

from sqlalchemy.orm import Session

from app.condition_trackers.service import (
    ConditionTrackerUpdateResult,
    TrackerUpdateIgnoredReason,
    update_condition_tracker,
)
from app.alerts.service import (
    process_consecutive_spo2_warning,
    process_immediate_critical_alert,
    process_persistent_hr_warning,
)
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    EventEvaluation,
    MonitoringState,
)
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.patients.model import ElderlyPatient


def _update_realtime_tracker(
    db: Session,
    event: HealthEvent,
    evaluation: EventEvaluation,
) -> ConditionTrackerUpdateResult:
    if event.validation_status != ValidationStatus.VALID_REALTIME:
        return ConditionTrackerUpdateResult(
            applied=False,
            ignored_reason=TrackerUpdateIgnoredReason.INELIGIBLE_VALIDATION_STATUS,
        )
    return update_condition_tracker(db, event, evaluation)


def evaluate_event(
    db: Session,
    event: HealthEvent,
) -> EventEvaluation | None:
    """
    Route a health event to its corresponding metric evaluator.

    Unsupported metrics are stored as health events but do not yet
    create event evaluations.
    """
    if event.metric_type == MetricType.HEART_RATE:
        return evaluate_heart_rate_event(db, event)

    if event.metric_type == MetricType.SPO2:
        return evaluate_spo2_event(db, event)

    return None


def evaluate_heart_rate_event(
    db: Session,
    event: HealthEvent,
) -> EventEvaluation | None:
    if event.metric_type != MetricType.HEART_RATE:
        return None

    if event.numeric_value is None:
        return None

    patient = db.get(ElderlyPatient, event.patient_id)

    if patient is None:
        raise ValueError("Patient not found")

    value = Decimal(event.numeric_value)

    if value > Decimal("150"):
        condition = ConditionKey.HR_HIGH
        threshold = Decimal("150")
        new_state = MonitoringState.CRITICAL
        severity = EvaluationSeverity.CRITICAL
        reason = (
            f"Heart rate {value} bpm exceeded the critical " "threshold of 150 bpm."
        )

    elif value > Decimal(patient.normal_hr_max):
        condition = ConditionKey.HR_HIGH
        threshold = Decimal(patient.normal_hr_max)
        new_state = MonitoringState.ELEVATED
        severity = EvaluationSeverity.WARNING
        reason = (
            f"Heart rate {value} bpm exceeded the patient's "
            f"normal maximum of {patient.normal_hr_max} bpm."
        )

    elif value < Decimal(patient.normal_hr_min):
        condition = ConditionKey.HR_LOW
        threshold = Decimal(patient.normal_hr_min)
        new_state = MonitoringState.ELEVATED
        severity = EvaluationSeverity.WARNING
        reason = (
            f"Heart rate {value} bpm fell below the patient's "
            f"normal minimum of {patient.normal_hr_min} bpm."
        )

    else:
        condition = ConditionKey.HR_NORMAL
        threshold = None
        new_state = MonitoringState.STABLE
        severity = EvaluationSeverity.INFO
        reason = f"Heart rate {value} bpm is within the patient's " "expected range."

    evaluation = EventEvaluation(
        event_id=event.event_id,
        alert_id=None,
        condition_key=condition,
        threshold_value_used=threshold,
        threshold_met=new_state != MonitoringState.STABLE,
        persistence_met=False,
        previous_state=MonitoringState.UNKNOWN,
        new_state=new_state,
        severity=severity,
        evaluation_reason=reason,
    )

    db.add(evaluation)
    db.flush()

    tracker_result = _update_realtime_tracker(
        db=db,
        event=event,
        evaluation=evaluation,
    )
    process_immediate_critical_alert(db, event, evaluation, tracker_result)
    process_persistent_hr_warning(db, event, evaluation, tracker_result)

    return evaluation


def evaluate_spo2_event(
    db: Session,
    event: HealthEvent,
) -> EventEvaluation | None:
    if event.metric_type != MetricType.SPO2:
        return None

    if event.numeric_value is None:
        return None

    patient = db.get(ElderlyPatient, event.patient_id)

    if patient is None:
        raise ValueError("Patient not found")

    value = Decimal(event.numeric_value)

    if value < Decimal("90"):
        condition = ConditionKey.SPO2_LOW
        threshold = Decimal("90")
        new_state = MonitoringState.CRITICAL
        severity = EvaluationSeverity.CRITICAL
        reason = f"SpO₂ {value}% fell below the critical " "threshold of 90%."

    elif value <= Decimal("93"):
        condition = ConditionKey.SPO2_LOW
        threshold = Decimal("93")
        new_state = MonitoringState.ELEVATED
        severity = EvaluationSeverity.WARNING
        reason = (
            f"SpO₂ {value}% is within the Warning range of 90% through 93%."
        )

    else:
        condition = ConditionKey.SPO2_NORMAL
        threshold = None
        new_state = MonitoringState.STABLE
        severity = EvaluationSeverity.INFO
        reason = f"SpO₂ {value}% is within the patient's " "expected range."

    evaluation = EventEvaluation(
        event_id=event.event_id,
        alert_id=None,
        condition_key=condition,
        threshold_value_used=threshold,
        threshold_met=new_state != MonitoringState.STABLE,
        persistence_met=False,
        previous_state=MonitoringState.UNKNOWN,
        new_state=new_state,
        severity=severity,
        evaluation_reason=reason,
    )

    db.add(evaluation)
    db.flush()

    tracker_result = _update_realtime_tracker(
        db=db,
        event=event,
        evaluation=evaluation,
    )
    process_immediate_critical_alert(db, event, evaluation, tracker_result)
    process_consecutive_spo2_warning(db, event, evaluation, tracker_result)

    return evaluation
