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

BASE_TIME = datetime(2026, 7, 25, 12, tzinfo=timezone.utc)


def ingest(
    db,
    patient,
    metric_type,
    value,
    recorded_at=BASE_TIME,
    validation_status=ValidationStatus.VALID_REALTIME,
    external_event_id=None,
):
    return create_health_event(
        db,
        HealthEventCreate(
            patient_id=patient.patient_id,
            external_event_id=external_event_id,
            metric_type=metric_type,
            numeric_value=value,
            recorded_at=recorded_at,
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


def unresolved_alerts(db, patient, condition=None):
    statement = select(Alert).where(
        Alert.patient_id == patient.patient_id,
        Alert.status.in_((AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED)),
    )
    if condition is not None:
        statement = statement.where(Alert.condition_key == condition)
    return db.scalars(statement).all()


@pytest.mark.parametrize(
    ("metric_type", "value", "condition"),
    [
        (MetricType.HEART_RATE, "151", ConditionKey.HR_HIGH),
        (MetricType.SPO2, "89", ConditionKey.SPO2_LOW),
    ],
)
def test_realtime_critical_event_creates_and_links_alert(
    db_session,
    patient,
    metric_type,
    value,
    condition,
):
    event = ingest(db_session, patient, metric_type, value)
    evaluation = evaluation_for(db_session, event)
    tracker = db_session.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient.patient_id,
            ConditionTracker.condition_key == condition,
        )
    )
    alerts = unresolved_alerts(db_session, patient, condition)

    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 1
    assert evaluation.severity is EvaluationSeverity.CRITICAL
    assert evaluation.condition_key is condition
    assert tracker.active is True
    assert tracker.last_event_id == event.event_id
    assert len(alerts) == 1
    assert alerts[0].status is AlertStatus.ACTIVE
    assert alerts[0].severity is EvaluationSeverity.CRITICAL
    assert alerts[0].detected_at == tracker.started_at
    assert alerts[0].confirmed_at == event.recorded_at
    assert evaluation.alert_id == alerts[0].alert_id


@pytest.mark.parametrize(
    ("metric_type", "value"),
    [
        (MetricType.HEART_RATE, "150"),
        (MetricType.SPO2, "90"),
    ],
)
def test_critical_boundary_does_not_create_alert(
    db_session,
    patient,
    metric_type,
    value,
):
    event = ingest(db_session, patient, metric_type, value)

    assert evaluation_for(db_session, event).severity is EvaluationSeverity.WARNING
    assert unresolved_alerts(db_session, patient) == []


@pytest.mark.parametrize(
    ("metric_type", "values", "condition"),
    [
        (MetricType.HEART_RATE, ("151", "165"), ConditionKey.HR_HIGH),
        (MetricType.SPO2, ("89", "85"), ConditionKey.SPO2_LOW),
    ],
)
def test_repeated_critical_events_reuse_one_alert(
    db_session,
    patient,
    metric_type,
    values,
    condition,
):
    first = ingest(db_session, patient, metric_type, values[0])
    second = ingest(
        db_session,
        patient,
        metric_type,
        values[1],
        BASE_TIME + timedelta(seconds=1),
    )
    alerts = unresolved_alerts(db_session, patient, condition)
    tracker_count = db_session.scalar(
        select(func.count(ConditionTracker.condition_tracker_id)).where(
            ConditionTracker.patient_id == patient.patient_id,
            ConditionTracker.condition_key == condition,
            ConditionTracker.active.is_(True),
        )
    )

    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 2
    assert db_session.scalar(select(func.count(EventEvaluation.evaluation_id))) == 2
    assert tracker_count == 1
    assert len(alerts) == 1
    assert evaluation_for(db_session, first).alert_id == alerts[0].alert_id
    assert evaluation_for(db_session, second).alert_id == alerts[0].alert_id


def test_acknowledged_alert_is_reused_without_reactivation(db_session, patient):
    first = ingest(db_session, patient, MetricType.HEART_RATE, "151")
    alert = db_session.get(Alert, evaluation_for(db_session, first).alert_id)
    alert.status = AlertStatus.ACKNOWLEDGED
    db_session.commit()

    second = ingest(
        db_session,
        patient,
        MetricType.HEART_RATE,
        "160",
        BASE_TIME + timedelta(seconds=1),
    )
    db_session.refresh(alert)

    assert alert.status is AlertStatus.ACKNOWLEDGED
    assert evaluation_for(db_session, second).alert_id == alert.alert_id
    assert len(unresolved_alerts(db_session, patient, ConditionKey.HR_HIGH)) == 1


def test_different_conditions_create_separate_alerts(db_session, patient):
    ingest(db_session, patient, MetricType.HEART_RATE, "151")
    ingest(db_session, patient, MetricType.SPO2, "89")

    assert {
        alert.condition_key for alert in unresolved_alerts(db_session, patient)
    } == {ConditionKey.HR_HIGH, ConditionKey.SPO2_LOW}


def test_stale_critical_event_does_not_link_or_touch_alert(db_session, patient):
    current = ingest(db_session, patient, MetricType.HEART_RATE, "151")
    alert = db_session.get(Alert, evaluation_for(db_session, current).alert_id)
    original_updated_at = alert.updated_at

    stale = ingest(
        db_session,
        patient,
        MetricType.HEART_RATE,
        "165",
        BASE_TIME - timedelta(minutes=1),
    )
    db_session.refresh(alert)

    assert evaluation_for(db_session, stale).alert_id is None
    assert alert.updated_at == original_updated_at
    assert len(unresolved_alerts(db_session, patient, ConditionKey.HR_HIGH)) == 1


def test_equal_timestamp_lower_priority_event_does_not_touch_alert(
    db_session,
    patient,
):
    critical = ingest(db_session, patient, MetricType.HEART_RATE, "151")
    alert = db_session.get(Alert, evaluation_for(db_session, critical).alert_id)
    original_updated_at = alert.updated_at

    lower_priority = ingest(
        db_session,
        patient,
        MetricType.HEART_RATE,
        "110",
        BASE_TIME,
    )
    db_session.refresh(alert)

    assert evaluation_for(db_session, lower_priority).alert_id is None
    assert alert.updated_at == original_updated_at


@pytest.mark.parametrize(
    "validation_status",
    [ValidationStatus.DELAYED_USABLE, ValidationStatus.INVALID],
)
def test_non_realtime_critical_value_creates_no_alert(
    db_session,
    patient,
    validation_status,
):
    event = ingest(
        db_session,
        patient,
        MetricType.HEART_RATE,
        "151",
        validation_status=validation_status,
    )

    assert unresolved_alerts(db_session, patient) == []
    evaluation = evaluation_for(db_session, event)
    if validation_status is ValidationStatus.INVALID:
        assert evaluation is None
    else:
        assert evaluation.alert_id is None


def test_identical_external_event_retry_is_alert_idempotent(db_session, patient):
    external_event_id = f"critical-{uuid4()}"
    first = ingest(
        db_session,
        patient,
        MetricType.HEART_RATE,
        "151",
        external_event_id=external_event_id,
    )
    second = ingest(
        db_session,
        patient,
        MetricType.HEART_RATE,
        "151",
        external_event_id=external_event_id,
    )

    assert second.event_id == first.event_id
    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 1
    assert db_session.scalar(select(func.count(EventEvaluation.evaluation_id))) == 1
    assert db_session.scalar(select(func.count(Alert.alert_id))) == 1


def test_alert_service_failure_rolls_back_pipeline(
    db_session,
    patient,
    monkeypatch,
):
    original = evaluation_service.process_immediate_critical_alert

    def create_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("alert service failed")

    monkeypatch.setattr(
        evaluation_service,
        "process_immediate_critical_alert",
        create_then_fail,
    )
    with pytest.raises(RuntimeError, match="alert service failed"):
        ingest(db_session, patient, MetricType.HEART_RATE, "151")

    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 0
    assert db_session.scalar(select(func.count(EventEvaluation.evaluation_id))) == 0
    assert db_session.scalar(
        select(func.count(ConditionTracker.condition_tracker_id))
    ) == 0
    assert db_session.scalar(select(func.count(Alert.alert_id))) == 0


def concurrent_ingest(engine, patient_id, specifications):
    barrier = Barrier(len(specifications))
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def worker(specification):
        session = factory()
        try:
            event = HealthEventCreate(
                patient_id=patient_id,
                external_event_id=f"critical-concurrent-{uuid4()}",
                validation_status=ValidationStatus.VALID_REALTIME,
                recorded_at=BASE_TIME,
                **specification,
            )
            barrier.wait()
            return create_health_event(session, event).event_id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=len(specifications)) as executor:
        return list(executor.map(worker, specifications))


def test_concurrent_first_critical_events_create_one_alert(
    integration_engine,
    db_session,
    patient,
):
    event_ids = concurrent_ingest(
        integration_engine,
        patient.patient_id,
        [
            {"metric_type": MetricType.HEART_RATE, "numeric_value": "151"},
            {"metric_type": MetricType.HEART_RATE, "numeric_value": "165"},
        ],
    )
    db_session.expire_all()
    alerts = unresolved_alerts(db_session, patient, ConditionKey.HR_HIGH)
    tracker = db_session.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient.patient_id,
            ConditionTracker.condition_key == ConditionKey.HR_HIGH,
        )
    )
    accepted_evaluation = evaluation_for(
        db_session,
        db_session.get(HealthEvent, tracker.last_event_id),
    )

    assert len(event_ids) == 2
    assert len(alerts) == 1
    assert accepted_evaluation.alert_id == alerts[0].alert_id


def test_concurrent_different_critical_conditions_create_separate_alerts(
    integration_engine,
    db_session,
    patient,
):
    concurrent_ingest(
        integration_engine,
        patient.patient_id,
        [
            {"metric_type": MetricType.HEART_RATE, "numeric_value": "151"},
            {"metric_type": MetricType.SPO2, "numeric_value": "89"},
        ],
    )
    db_session.expire_all()

    assert {
        alert.condition_key for alert in unresolved_alerts(db_session, patient)
    } == {ConditionKey.HR_HIGH, ConditionKey.SPO2_LOW}
