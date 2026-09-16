from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import app.event_evaluations.service as evaluation_service
from app.alerts.model import Alert, AlertStatus
from app.condition_trackers.model import ConditionTracker
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    EventEvaluation,
)
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event

pytestmark = pytest.mark.integration

BASE_TIME = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
WARNING_OFFSETS = tuple(range(0, 120, 15))


def ingest(
    db,
    patient,
    value,
    offset_seconds,
    *,
    validation_status=ValidationStatus.VALID_REALTIME,
    external_event_id=None,
):
    return create_health_event(
        db,
        HealthEventCreate(
            patient_id=patient.patient_id,
            external_event_id=external_event_id,
            metric_type=MetricType.HEART_RATE,
            numeric_value=value,
            recorded_at=BASE_TIME + timedelta(seconds=offset_seconds),
            validation_status=validation_status,
            validation_reason=(
                "retained for audit"
                if validation_status != ValidationStatus.VALID_REALTIME
                else None
            ),
        ),
    )


def sustain(db, patient, value="110", offsets=WARNING_OFFSETS):
    return [ingest(db, patient, value, offset) for offset in offsets]


def evaluation_for(db, event):
    return db.scalar(
        select(EventEvaluation).where(EventEvaluation.event_id == event.event_id)
    )


def tracker_for(db, patient, condition=ConditionKey.HR_HIGH):
    return db.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient.patient_id,
            ConditionTracker.condition_key == condition,
        )
    )


def alerts_for(db, patient, condition=ConditionKey.HR_HIGH):
    return db.scalars(
        select(Alert)
        .where(
            Alert.patient_id == patient.patient_id,
            Alert.condition_key == condition,
        )
        .order_by(Alert.created_at)
    ).all()


def qualify_warning(db, patient, value="110"):
    sustain(db, patient, value)
    return ingest(db, patient, value, 120)


def test_hr_high_before_two_minutes_has_no_warning_alert(db_session, patient):
    sustain(db_session, patient)
    event = ingest(db_session, patient, "110", 119)
    evaluation = evaluation_for(db_session, event)

    assert alerts_for(db_session, patient) == []
    assert evaluation.persistence_met is False
    assert evaluation.alert_id is None


@pytest.mark.parametrize(
    ("value", "condition"),
    [("110", ConditionKey.HR_HIGH), ("50", ConditionKey.HR_LOW)],
)
def test_hr_warning_qualifies_exactly_at_two_event_minutes(
    db_session,
    patient,
    value,
    condition,
):
    event = qualify_warning(db_session, patient, value)
    evaluation = evaluation_for(db_session, event)
    alert = alerts_for(db_session, patient, condition)[0]

    assert evaluation.severity is EvaluationSeverity.WARNING
    assert evaluation.persistence_met is True
    assert evaluation.alert_id == alert.alert_id
    assert alert.severity is EvaluationSeverity.WARNING
    assert alert.status is AlertStatus.ACTIVE
    assert alert.detected_at == BASE_TIME
    assert alert.confirmed_at == BASE_TIME + timedelta(minutes=2)


def test_persistence_uses_event_timestamps_without_waiting(db_session, patient):
    event = qualify_warning(db_session, patient)

    assert evaluation_for(db_session, event).persistence_met is True


def test_two_isolated_normal_buckets_do_not_kill_sustained_warning(
    db_session,
    patient,
):
    values = ("110", "112", "115", "78", "116", "118", "80", "120", "114")
    events = [
        ingest(db_session, patient, value, index * 15)
        for index, value in enumerate(values)
    ]

    alert = alerts_for(db_session, patient)[0]
    assert evaluation_for(db_session, events[-1]).alert_id == alert.alert_id


def test_repeated_instability_qualifies_intermittent_warning(db_session, patient):
    values = ("110", "78", "78", "110", "110", "78", "78", "110", "110", "110")
    events = [
        ingest(db_session, patient, value, index * 30)
        for index, value in enumerate(values)
    ]

    alert = alerts_for(db_session, patient)[0]
    evaluation = evaluation_for(db_session, events[-1])
    assert evaluation.alert_id == alert.alert_id
    assert "intermittent abnormality" in evaluation.evaluation_reason


@pytest.mark.parametrize("gap_seconds", [89, 90])
def test_gap_up_to_ninety_seconds_preserves_occurrence(
    db_session,
    patient,
    gap_seconds,
):
    first = ingest(db_session, patient, "110", 0)
    second = ingest(db_session, patient, "110", gap_seconds)
    tracker = tracker_for(db_session, patient)

    assert tracker.started_at == first.recorded_at
    assert tracker.last_event_id == second.event_id


def test_gap_over_ninety_seconds_restarts_occurrence(db_session, patient):
    ingest(db_session, patient, "110", 0)
    restarted = ingest(db_session, patient, "110", 91)
    tracker = tracker_for(db_session, patient)
    evaluation = evaluation_for(db_session, restarted)

    assert tracker.started_at == restarted.recorded_at
    assert evaluation.persistence_met is False
    assert evaluation.alert_id is None


def test_ninety_seconds_of_normal_readings_ends_occurrence(
    db_session,
    patient,
):
    ingest(db_session, patient, "110", 0)
    for offset in range(15, 105, 15):
        ingest(db_session, patient, "78", offset)
    tracker = tracker_for(db_session, patient)
    assert tracker.active is True

    ingest(db_session, patient, "78", 105)
    assert tracker_for(db_session, patient).active is False

    restarted = ingest(db_session, patient, "110", 120)
    assert tracker_for(db_session, patient).started_at == restarted.recorded_at
    assert evaluation_for(db_session, restarted).persistence_met is False


def test_planned_thirty_second_gap_preserves_hr_occurrence(db_session, patient):
    first = ingest(db_session, patient, "110", 0)
    ingest(db_session, patient, "110", 30)

    assert tracker_for(db_session, patient).started_at == first.recorded_at


def test_stale_event_does_not_advance_persistence_or_tracker(db_session, patient):
    ingest(db_session, patient, "110", 0)
    current = ingest(db_session, patient, "110", 90)
    stale = ingest(db_session, patient, "110", 30)
    tracker = tracker_for(db_session, patient)

    assert tracker.last_event_id == current.event_id
    assert tracker.last_seen_at == current.recorded_at
    assert evaluation_for(db_session, stale).persistence_met is False
    assert evaluation_for(db_session, stale).alert_id is None


def test_equal_timestamp_lower_priority_does_not_advance_persistence(
    db_session,
    patient,
):
    ingest(db_session, patient, "110", 0)
    critical = ingest(db_session, patient, "151", 60)
    rejected = ingest(db_session, patient, "110", 60)
    tracker = tracker_for(db_session, patient)

    assert tracker.last_event_id == critical.event_id
    assert evaluation_for(db_session, rejected).persistence_met is False
    assert evaluation_for(db_session, rejected).alert_id is None


@pytest.mark.parametrize(
    "validation_status",
    [ValidationStatus.DELAYED_USABLE, ValidationStatus.INVALID],
)
def test_non_realtime_hr_does_not_advance_persistence(
    db_session,
    patient,
    validation_status,
):
    current = ingest(db_session, patient, "110", 0)
    retained = ingest(
        db_session,
        patient,
        "110",
        90,
        validation_status=validation_status,
    )
    tracker = tracker_for(db_session, patient)

    assert tracker.last_event_id == current.event_id
    assert alerts_for(db_session, patient) == []
    evaluation = evaluation_for(db_session, retained)
    if validation_status is ValidationStatus.INVALID:
        assert evaluation is None
    else:
        assert evaluation.persistence_met is False
        assert evaluation.alert_id is None


def test_later_qualified_warning_evaluations_reuse_alert(db_session, patient):
    qualifying = qualify_warning(db_session, patient)
    later = ingest(db_session, patient, "115", 135)
    alerts = alerts_for(db_session, patient)

    assert len(alerts) == 1
    assert evaluation_for(db_session, qualifying).alert_id == alerts[0].alert_id
    assert evaluation_for(db_session, later).alert_id == alerts[0].alert_id
    assert evaluation_for(db_session, later).persistence_met is True


def test_acknowledged_warning_is_reused_and_stays_acknowledged(
    db_session,
    patient,
):
    qualifying = qualify_warning(db_session, patient)
    alert = db_session.get(Alert, evaluation_for(db_session, qualifying).alert_id)
    alert.status = AlertStatus.ACKNOWLEDGED
    db_session.commit()

    later = ingest(db_session, patient, "115", 135)
    db_session.refresh(alert)

    assert alert.status is AlertStatus.ACKNOWLEDGED
    assert alert.severity is EvaluationSeverity.WARNING
    assert evaluation_for(db_session, later).alert_id == alert.alert_id


@pytest.mark.parametrize("acknowledged", [False, True])
def test_warning_to_critical_reuses_and_escalates_same_alert(
    db_session,
    patient,
    acknowledged,
):
    qualifying = qualify_warning(db_session, patient)
    alert = db_session.get(Alert, evaluation_for(db_session, qualifying).alert_id)
    if acknowledged:
        alert.status = AlertStatus.ACKNOWLEDGED
        db_session.commit()

    first_critical = ingest(db_session, patient, "151", 135)
    critical = ingest(db_session, patient, "155", 150)
    db_session.refresh(alert)

    assert len(alerts_for(db_session, patient)) == 1
    assert evaluation_for(db_session, first_critical).alert_id is None
    assert evaluation_for(db_session, critical).alert_id == alert.alert_id
    assert alert.severity is EvaluationSeverity.CRITICAL
    assert alert.status is (
        AlertStatus.ACKNOWLEDGED if acknowledged else AlertStatus.ACTIVE
    )


def test_confirmed_critical_prevents_later_warning_duplicate(
    db_session,
    patient,
):
    for offset in range(0, 90, 15):
        ingest(db_session, patient, "110", offset)
    ingest(db_session, patient, "151", 90)
    critical = ingest(db_session, patient, "155", 105)
    later = ingest(db_session, patient, "110", 120)
    alerts = alerts_for(db_session, patient)

    assert len(alerts) == 1
    assert alerts[0].severity is EvaluationSeverity.CRITICAL
    assert alerts[0].detected_at == BASE_TIME
    assert alerts[0].confirmed_at == critical.recorded_at
    assert evaluation_for(db_session, later).persistence_met is True
    assert evaluation_for(db_session, later).alert_id == alerts[0].alert_id


def resolve_alert(db, alert):
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = alert.confirmed_at
    db.commit()


def test_resolved_warning_suppresses_duplicate_in_same_occurrence(
    db_session,
    patient,
):
    qualifying = qualify_warning(db_session, patient)
    alert = db_session.get(Alert, evaluation_for(db_session, qualifying).alert_id)
    resolve_alert(db_session, alert)

    later = ingest(db_session, patient, "115", 135)

    assert len(alerts_for(db_session, patient)) == 1
    assert evaluation_for(db_session, later).persistence_met is True
    assert evaluation_for(db_session, later).alert_id == alert.alert_id
    assert alert.status is AlertStatus.RESOLVED


def test_normalization_allows_warning_in_new_occurrence(db_session, patient):
    qualifying = qualify_warning(db_session, patient)
    first_alert = db_session.get(
        Alert,
        evaluation_for(db_session, qualifying).alert_id,
    )
    resolve_alert(db_session, first_alert)
    for offset in range(135, 240, 15):
        ingest(db_session, patient, "78", offset)

    for offset in range(240, 360, 15):
        ingest(db_session, patient, "110", offset)
    new_qualifying = ingest(db_session, patient, "110", 360)

    alerts = alerts_for(db_session, patient)
    assert len(alerts) == 2
    assert evaluation_for(db_session, new_qualifying).alert_id == alerts[1].alert_id
    assert alerts[1].detected_at == BASE_TIME + timedelta(seconds=240)


@pytest.mark.parametrize(
    "first_status",
    [AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED],
)
def test_new_warning_occurrence_does_not_reuse_prior_unresolved_alert(
    db_session,
    patient,
    first_status,
):
    qualifying = qualify_warning(db_session, patient)
    first_alert = db_session.get(
        Alert,
        evaluation_for(db_session, qualifying).alert_id,
    )
    first_alert.status = first_status
    db_session.commit()
    for offset in range(135, 240, 15):
        ingest(db_session, patient, "78", offset)

    for offset in range(240, 360, 15):
        ingest(db_session, patient, "110", offset)
    recurring = ingest(db_session, patient, "110", 360)

    alerts = alerts_for(db_session, patient)
    assert len(alerts) == 2
    assert evaluation_for(db_session, recurring).alert_id == alerts[1].alert_id
    assert alerts[1].alert_id != first_alert.alert_id
    assert alerts[1].detected_at == BASE_TIME + timedelta(seconds=240)
    assert first_alert.status is first_status


def test_resolved_warning_allows_critical_safety_exception(
    db_session,
    patient,
):
    qualifying = qualify_warning(db_session, patient)
    warning = db_session.get(Alert, evaluation_for(db_session, qualifying).alert_id)
    resolve_alert(db_session, warning)

    first_critical = ingest(db_session, patient, "151", 135)
    critical = ingest(db_session, patient, "155", 150)
    alerts = alerts_for(db_session, patient)

    assert len(alerts) == 2
    assert alerts[0].status is AlertStatus.RESOLVED
    assert alerts[1].severity is EvaluationSeverity.CRITICAL
    assert evaluation_for(db_session, first_critical).alert_id is None
    assert evaluation_for(db_session, critical).alert_id == alerts[1].alert_id


def test_duplicate_retry_does_not_advance_persistence_twice(db_session, patient):
    sustain(db_session, patient)
    external_event_id = f"warning-boundary-{uuid4()}"
    first = ingest(
        db_session,
        patient,
        "110",
        120,
        external_event_id=external_event_id,
    )
    event_count = db_session.scalar(select(func.count(HealthEvent.event_id)))
    second = ingest(
        db_session,
        patient,
        "110",
        120,
        external_event_id=external_event_id,
    )

    assert second.event_id == first.event_id
    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == event_count
    assert len(alerts_for(db_session, patient)) == 1


def concurrent_ingest(engine, patient_id, specifications):
    barrier = Barrier(len(specifications))
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def worker(specification):
        session = factory()
        try:
            event = HealthEventCreate(
                patient_id=patient_id,
                external_event_id=f"warning-concurrent-{uuid4()}",
                metric_type=MetricType.HEART_RATE,
                validation_status=ValidationStatus.VALID_REALTIME,
                **specification,
            )
            barrier.wait()
            return create_health_event(session, event).event_id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=len(specifications)) as executor:
        return list(executor.map(worker, specifications))


def test_concurrent_boundary_events_create_one_warning(
    integration_engine,
    db_session,
    patient,
):
    sustain(db_session, patient)
    concurrent_ingest(
        integration_engine,
        patient.patient_id,
        [
            {
                "numeric_value": "110",
                "recorded_at": BASE_TIME + timedelta(seconds=120),
            },
            {
                "numeric_value": "115",
                "recorded_at": BASE_TIME + timedelta(seconds=121),
            },
        ],
    )
    db_session.expire_all()

    assert len(alerts_for(db_session, patient)) == 1


def test_warning_alert_failure_rolls_back_boundary_event(
    db_session,
    patient,
    monkeypatch,
):
    sustain(db_session, patient)
    tracker = tracker_for(db_session, patient)
    prior_event_count = db_session.scalar(select(func.count(HealthEvent.event_id)))
    original = evaluation_service.process_persistent_hr_warning

    def create_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("warning alert failed")

    monkeypatch.setattr(
        evaluation_service,
        "process_persistent_hr_warning",
        create_then_fail,
    )
    with pytest.raises(RuntimeError, match="warning alert failed"):
        ingest(db_session, patient, "110", 120)

    db_session.refresh(tracker)
    assert (
        db_session.scalar(select(func.count(HealthEvent.event_id)))
        == prior_event_count
    )
    assert tracker.last_seen_at == BASE_TIME + timedelta(seconds=105)
    assert len(alerts_for(db_session, patient)) == 0
