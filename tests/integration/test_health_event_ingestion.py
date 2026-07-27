from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.condition_trackers.model import ConditionTracker
from app.event_evaluations.model import EventEvaluation, MonitoringState
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event

pytestmark = pytest.mark.integration


def create(db_session, patient, **changes):
    payload = {
        "patient_id": patient.patient_id,
        "metric_type": MetricType.HEART_RATE,
        "numeric_value": Decimal("78"),
        "recorded_at": datetime(2026, 7, 17, 5, tzinfo=timezone.utc),
        "validation_status": ValidationStatus.VALID_REALTIME,
    }
    payload.update(changes)
    return create_health_event(db_session, HealthEventCreate.model_validate(payload))


def test_ingestion_persists_event_evaluation_and_tracker(db_session, patient):
    event = create(db_session, patient, numeric_value="110")
    evaluation = db_session.scalar(
        select(EventEvaluation).where(EventEvaluation.event_id == event.event_id)
    )
    tracker = db_session.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient.patient_id,
            ConditionTracker.active.is_(True),
        )
    )
    assert evaluation.new_state == MonitoringState.ELEVATED
    assert tracker.last_event_id == event.event_id


def test_normalized_value_is_used_for_storage_and_evaluation(db_session, patient):
    event = create(db_session, patient, numeric_value="150.995")
    evaluation = db_session.scalar(
        select(EventEvaluation).where(EventEvaluation.event_id == event.event_id)
    )
    assert event.numeric_value == Decimal("151.00")
    assert evaluation.new_state == MonitoringState.CRITICAL
    assert "151.00" in evaluation.evaluation_reason


@pytest.mark.parametrize("metric_type", [MetricType.HEART_RATE, MetricType.SPO2])
def test_invalid_metric_is_stored_without_evaluation_or_tracker(
    db_session,
    patient,
    metric_type,
):
    event = create(
        db_session,
        patient,
        metric_type=metric_type,
        numeric_value=None,
        validation_status=ValidationStatus.INVALID,
        validation_reason="sensor quality failure",
    )
    assert db_session.get(HealthEvent, event.event_id) is not None
    assert db_session.scalar(select(func.count(EventEvaluation.evaluation_id))) == 0
    assert db_session.scalar(select(func.count(ConditionTracker.condition_tracker_id))) == 0


def test_delayed_usable_new_event_does_not_update_realtime_tracker(
    db_session,
    patient,
):
    event = create(
        db_session,
        patient,
        numeric_value="110",
        validation_status=ValidationStatus.DELAYED_USABLE,
        validation_reason="uploaded after reconnect",
    )
    assert db_session.get(HealthEvent, event.event_id) is not None
    assert db_session.scalar(select(EventEvaluation)) is not None
    assert db_session.scalar(select(ConditionTracker)) is None
