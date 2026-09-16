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
    MonitoringState,
)
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event

pytestmark = pytest.mark.integration

BASE_TIME = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)


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
            metric_type=MetricType.SPO2,
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


def evaluation_for(db, event):
    return db.scalar(
        select(EventEvaluation).where(EventEvaluation.event_id == event.event_id)
    )


def tracker_for(db, patient):
    return db.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient.patient_id,
            ConditionTracker.condition_key == ConditionKey.SPO2_LOW,
        )
    )


def alerts_for(db, patient, condition=ConditionKey.SPO2_LOW):
    return db.scalars(
        select(Alert)
        .where(
            Alert.patient_id == patient.patient_id,
            Alert.condition_key == condition,
        )
        .order_by(Alert.created_at)
    ).all()


@pytest.mark.parametrize("value", ["90", "93"])
def test_first_warning_candidate_starts_count_without_alert(
    db_session,
    patient,
    value,
):
    event = ingest(db_session, patient, value, 0)
    evaluation = evaluation_for(db_session, event)

    assert tracker_for(db_session, patient).consecutive_event_count == 1
    assert alerts_for(db_session, patient) == []
    assert evaluation.persistence_met is False
    assert evaluation.alert_id is None


def test_second_warning_candidate_qualifies_and_links(db_session, patient):
    first = ingest(db_session, patient, "92", 0)
    second = ingest(db_session, patient, "93", 60)
    alert = alerts_for(db_session, patient)[0]

    assert evaluation_for(db_session, first).persistence_met is False
    assert evaluation_for(db_session, second).persistence_met is True
    assert evaluation_for(db_session, second).alert_id == alert.alert_id
    assert alert.severity is EvaluationSeverity.WARNING
    assert alert.status is AlertStatus.ACTIVE
    assert alert.detected_at == first.recorded_at
    assert alert.confirmed_at == second.recorded_at


def test_spo2_94_is_normal_and_resets_occurrence(db_session, patient):
    ingest(db_session, patient, "92", 0)
    normal = ingest(db_session, patient, "94", 60)
    tracker = tracker_for(db_session, patient)

    assert evaluation_for(db_session, normal).new_state is MonitoringState.STABLE
    assert tracker.active is False
    assert tracker.consecutive_event_count == 0


def test_two_spo2_critical_samples_create_alert(db_session, patient):
    first = ingest(db_session, patient, "89", 0)
    event = ingest(db_session, patient, "85", 15)
    alert = alerts_for(db_session, patient)[0]

    assert alert.severity is EvaluationSeverity.CRITICAL
    assert evaluation_for(db_session, first).alert_id is None
    assert evaluation_for(db_session, event).alert_id == alert.alert_id
    assert tracker_for(db_session, patient).consecutive_event_count == 0


@pytest.mark.parametrize("gap_seconds", [299, 300])
def test_gap_up_to_five_minutes_preserves_occurrence_and_count(
    db_session,
    patient,
    gap_seconds,
):
    first = ingest(db_session, patient, "92", 0)
    ingest(db_session, patient, "93", gap_seconds)
    tracker = tracker_for(db_session, patient)

    assert tracker.started_at == first.recorded_at
    assert tracker.consecutive_event_count == 2
    assert len(alerts_for(db_session, patient)) == 1


def test_gap_over_five_minutes_restarts_count(db_session, patient):
    ingest(db_session, patient, "92", 0)
    restarted = ingest(db_session, patient, "93", 301)
    tracker = tracker_for(db_session, patient)
    evaluation = evaluation_for(db_session, restarted)

    assert tracker.started_at == restarted.recorded_at
    assert tracker.consecutive_event_count == 1
    assert evaluation.persistence_met is False
    assert evaluation.alert_id is None
    assert alerts_for(db_session, patient) == []


def test_normal_between_candidates_resets_count(db_session, patient):
    ingest(db_session, patient, "92", 0)
    ingest(db_session, patient, "94", 60)
    candidate = ingest(db_session, patient, "93", 120)

    assert tracker_for(db_session, patient).consecutive_event_count == 1
    assert evaluation_for(db_session, candidate).persistence_met is False
    assert alerts_for(db_session, patient) == []


def test_stale_event_does_not_increment_count(db_session, patient):
    current = ingest(db_session, patient, "92", 60)
    stale = ingest(db_session, patient, "93", 0)
    tracker = tracker_for(db_session, patient)

    assert tracker.last_event_id == current.event_id
    assert tracker.consecutive_event_count == 1
    assert evaluation_for(db_session, stale).persistence_met is False


def test_equal_timestamp_lower_priority_does_not_increment_count(
    db_session,
    patient,
):
    critical = ingest(db_session, patient, "89", 0)
    rejected = ingest(db_session, patient, "92", 0)
    tracker = tracker_for(db_session, patient)

    assert tracker.last_event_id == critical.event_id
    assert tracker.consecutive_event_count == 0
    assert evaluation_for(db_session, rejected).alert_id is None


@pytest.mark.parametrize(
    "validation_status",
    [ValidationStatus.DELAYED_USABLE, ValidationStatus.INVALID],
)
def test_non_realtime_candidate_does_not_increment_or_alert(
    db_session,
    patient,
    validation_status,
):
    first = ingest(db_session, patient, "92", 0)
    retained = ingest(
        db_session,
        patient,
        "93",
        60,
        validation_status=validation_status,
    )

    assert tracker_for(db_session, patient).last_event_id == first.event_id
    assert tracker_for(db_session, patient).consecutive_event_count == 1
    assert alerts_for(db_session, patient) == []
    evaluation = evaluation_for(db_session, retained)
    if validation_status is ValidationStatus.INVALID:
        assert evaluation is None
    else:
        assert evaluation.persistence_met is False


def test_third_and_later_candidates_reuse_same_alert(db_session, patient):
    ingest(db_session, patient, "92", 0)
    second = ingest(db_session, patient, "93", 60)
    third = ingest(db_session, patient, "91", 120)
    alert = alerts_for(db_session, patient)[0]

    assert len(alerts_for(db_session, patient)) == 1
    assert evaluation_for(db_session, second).alert_id == alert.alert_id
    assert evaluation_for(db_session, third).alert_id == alert.alert_id
    assert tracker_for(db_session, patient).consecutive_event_count == 3


def test_acknowledged_warning_is_reused(db_session, patient):
    ingest(db_session, patient, "92", 0)
    second = ingest(db_session, patient, "93", 60)
    alert = db_session.get(Alert, evaluation_for(db_session, second).alert_id)
    alert.status = AlertStatus.ACKNOWLEDGED
    db_session.commit()

    third = ingest(db_session, patient, "91", 120)
    db_session.refresh(alert)

    assert alert.status is AlertStatus.ACKNOWLEDGED
    assert evaluation_for(db_session, third).alert_id == alert.alert_id


@pytest.mark.parametrize("acknowledged", [False, True])
def test_warning_to_critical_reuses_and_escalates_alert(
    db_session,
    patient,
    acknowledged,
):
    ingest(db_session, patient, "92", 0)
    second = ingest(db_session, patient, "93", 60)
    alert = db_session.get(Alert, evaluation_for(db_session, second).alert_id)
    if acknowledged:
        alert.status = AlertStatus.ACKNOWLEDGED
        db_session.commit()

    first_critical = ingest(db_session, patient, "89", 120)
    critical = ingest(db_session, patient, "85", 135)
    db_session.refresh(alert)

    assert len(alerts_for(db_session, patient)) == 1
    assert alert.severity is EvaluationSeverity.CRITICAL
    assert alert.status is (
        AlertStatus.ACKNOWLEDGED if acknowledged else AlertStatus.ACTIVE
    )
    assert evaluation_for(db_session, first_critical).alert_id is None
    assert evaluation_for(db_session, critical).alert_id == alert.alert_id


def test_critical_before_second_candidate_prevents_warning_duplicate(
    db_session,
    patient,
):
    first = ingest(db_session, patient, "92", 0)
    ingest(db_session, patient, "89", 210)
    critical = ingest(db_session, patient, "85", 225)
    later = ingest(db_session, patient, "93", 240)
    alert = alerts_for(db_session, patient)[0]

    assert len(alerts_for(db_session, patient)) == 1
    assert alert.severity is EvaluationSeverity.CRITICAL
    assert alert.detected_at == first.recorded_at
    assert alert.confirmed_at == critical.recorded_at
    assert evaluation_for(db_session, later).persistence_met is True
    assert evaluation_for(db_session, later).alert_id == alert.alert_id


def test_critical_followed_by_warning_links_without_downgrade(
    db_session,
    patient,
):
    first_critical = ingest(db_session, patient, "89", 0)
    critical = ingest(db_session, patient, "85", 15)
    warning = ingest(db_session, patient, "92", 60)
    alert = alerts_for(db_session, patient)[0]

    assert alert.severity is EvaluationSeverity.CRITICAL
    assert evaluation_for(db_session, warning).alert_id == alert.alert_id
    assert evaluation_for(db_session, warning).persistence_met is True
    assert evaluation_for(db_session, first_critical).alert_id is None
    assert evaluation_for(db_session, critical).alert_id == alert.alert_id


def resolve_alert(db, alert):
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = alert.confirmed_at
    db.commit()


def test_resolved_warning_suppresses_same_occurrence_duplicate(
    db_session,
    patient,
):
    ingest(db_session, patient, "92", 0)
    second = ingest(db_session, patient, "93", 60)
    alert = db_session.get(Alert, evaluation_for(db_session, second).alert_id)
    resolve_alert(db_session, alert)

    later = ingest(db_session, patient, "91", 120)

    assert len(alerts_for(db_session, patient)) == 1
    assert evaluation_for(db_session, later).alert_id == alert.alert_id
    assert alert.status is AlertStatus.RESOLVED


def test_normalization_allows_new_warning_occurrence(db_session, patient):
    ingest(db_session, patient, "92", 0)
    second = ingest(db_session, patient, "93", 60)
    first_alert = db_session.get(Alert, evaluation_for(db_session, second).alert_id)
    resolve_alert(db_session, first_alert)
    ingest(db_session, patient, "94", 120)
    ingest(db_session, patient, "92", 180)
    qualifying = ingest(db_session, patient, "93", 240)

    alerts = alerts_for(db_session, patient)
    assert len(alerts) == 2
    assert evaluation_for(db_session, qualifying).alert_id == alerts[1].alert_id


@pytest.mark.parametrize(
    "first_status",
    [AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED],
)
def test_new_warning_occurrence_does_not_reuse_prior_unresolved_alert(
    db_session,
    patient,
    first_status,
):
    ingest(db_session, patient, "92", 0)
    second = ingest(db_session, patient, "93", 60)
    first_alert = db_session.get(Alert, evaluation_for(db_session, second).alert_id)
    first_alert.status = first_status
    db_session.commit()
    ingest(db_session, patient, "94", 120)
    ingest(db_session, patient, "92", 180)
    qualifying = ingest(db_session, patient, "93", 240)

    alerts = alerts_for(db_session, patient)
    assert len(alerts) == 2
    assert evaluation_for(db_session, qualifying).alert_id == alerts[1].alert_id
    assert alerts[1].alert_id != first_alert.alert_id
    assert first_alert.status is first_status


def test_resolved_warning_allows_critical_safety_exception(
    db_session,
    patient,
):
    ingest(db_session, patient, "92", 0)
    second = ingest(db_session, patient, "93", 60)
    warning = db_session.get(Alert, evaluation_for(db_session, second).alert_id)
    resolve_alert(db_session, warning)

    first_critical = ingest(db_session, patient, "89", 120)
    critical = ingest(db_session, patient, "85", 135)
    alerts = alerts_for(db_session, patient)

    assert len(alerts) == 2
    assert alerts[1].severity is EvaluationSeverity.CRITICAL
    assert evaluation_for(db_session, first_critical).alert_id is None
    assert evaluation_for(db_session, critical).alert_id == alerts[1].alert_id


def test_duplicate_retry_does_not_increment_count_twice(db_session, patient):
    ingest(db_session, patient, "92", 0)
    external_event_id = f"spo2-boundary-{uuid4()}"
    first = ingest(
        db_session,
        patient,
        "93",
        60,
        external_event_id=external_event_id,
    )
    second = ingest(
        db_session,
        patient,
        "93",
        60,
        external_event_id=external_event_id,
    )

    assert second.event_id == first.event_id
    assert tracker_for(db_session, patient).consecutive_event_count == 2
    assert len(alerts_for(db_session, patient)) == 1


def concurrent_ingest(engine, patient_id, specifications):
    barrier = Barrier(len(specifications))
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def worker(specification):
        session = factory()
        try:
            event = HealthEventCreate(
                patient_id=patient_id,
                external_event_id=f"spo2-concurrent-{uuid4()}",
                validation_status=ValidationStatus.VALID_REALTIME,
                **specification,
            )
            barrier.wait()
            return create_health_event(session, event).event_id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=len(specifications)) as executor:
        return list(executor.map(worker, specifications))


def test_concurrent_second_candidates_create_one_warning(
    integration_engine,
    db_session,
    patient,
):
    ingest(db_session, patient, "92", 0)
    concurrent_ingest(
        integration_engine,
        patient.patient_id,
        [
            {
                "metric_type": MetricType.SPO2,
                "numeric_value": "93",
                "recorded_at": BASE_TIME + timedelta(seconds=60),
            },
            {
                "metric_type": MetricType.SPO2,
                "numeric_value": "91",
                "recorded_at": BASE_TIME + timedelta(seconds=61),
            },
        ],
    )
    db_session.expire_all()

    assert len(alerts_for(db_session, patient)) == 1


def test_concurrent_different_conditions_remain_independent(
    integration_engine,
    db_session,
    patient,
):
    ingest(db_session, patient, "92", 0)
    for offset in (0, 90, 180, 270):
        create_health_event(
            db_session,
            HealthEventCreate(
                patient_id=patient.patient_id,
                metric_type=MetricType.HEART_RATE,
                numeric_value="110",
                recorded_at=BASE_TIME + timedelta(seconds=offset),
                validation_status=ValidationStatus.VALID_REALTIME,
            ),
        )

    concurrent_ingest(
        integration_engine,
        patient.patient_id,
        [
            {
                "metric_type": MetricType.SPO2,
                "numeric_value": "93",
                "recorded_at": BASE_TIME + timedelta(seconds=60),
            },
            {
                "metric_type": MetricType.HEART_RATE,
                "numeric_value": "110",
                "recorded_at": BASE_TIME + timedelta(seconds=300),
            },
        ],
    )
    db_session.expire_all()
    alerts = db_session.scalars(
        select(Alert).where(Alert.patient_id == patient.patient_id)
    ).all()

    assert {alert.condition_key for alert in alerts} == {
        ConditionKey.SPO2_LOW,
        ConditionKey.HR_HIGH,
    }


def test_alert_failure_rolls_back_count_and_boundary_event(
    db_session,
    patient,
    monkeypatch,
):
    first = ingest(db_session, patient, "92", 0)
    tracker = tracker_for(db_session, patient)
    original = evaluation_service.process_consecutive_spo2_warning

    def create_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("SpO2 warning alert failed")

    monkeypatch.setattr(
        evaluation_service,
        "process_consecutive_spo2_warning",
        create_then_fail,
    )
    with pytest.raises(RuntimeError, match="SpO2 warning alert failed"):
        ingest(db_session, patient, "93", 60)

    db_session.refresh(tracker)
    assert tracker.last_event_id == first.event_id
    assert tracker.consecutive_event_count == 1
    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 1
    assert alerts_for(db_session, patient) == []
