from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.condition_trackers.model import ConditionTracker
from app.event_evaluations.model import ConditionKey
from app.health_events.model import MetricType, ValidationStatus
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event

pytestmark = pytest.mark.integration
BASE_TIME = datetime(2026, 7, 17, 5, tzinfo=timezone.utc)


def ingest(db, patient, value, recorded_at, status=ValidationStatus.VALID_REALTIME):
    return create_health_event(
        db,
        HealthEventCreate(
            patient_id=patient.patient_id,
            metric_type=MetricType.HEART_RATE,
            numeric_value=value,
            recorded_at=recorded_at,
            validation_status=status,
            validation_reason=("delayed" if status == ValidationStatus.DELAYED_USABLE else None),
        ),
    )


def tracker(db, patient, condition=ConditionKey.HR_HIGH):
    return db.scalar(
        select(ConditionTracker).where(
            ConditionTracker.patient_id == patient.patient_id,
            ConditionTracker.condition_key == condition,
        )
    )


def test_new_abnormal_then_older_normal_keeps_active_tracker(db_session, patient):
    current = ingest(db_session, patient, "110", BASE_TIME)
    ingest(db_session, patient, "78", BASE_TIME - timedelta(minutes=1))
    result = tracker(db_session, patient)
    assert result.active is True
    assert result.last_event_id == current.event_id
    assert result.last_seen_at == BASE_TIME


def test_new_normal_then_older_abnormal_keeps_inactive_watermark(db_session, patient):
    current = ingest(db_session, patient, "78", BASE_TIME)
    ingest(db_session, patient, "110", BASE_TIME - timedelta(minutes=1))
    result = tracker(db_session, patient)
    assert result.active is False
    assert result.last_event_id == current.event_id


def test_new_critical_then_older_elevated_stays_confirmed(db_session, patient):
    ingest(db_session, patient, "165", BASE_TIME)
    current = ingest(db_session, patient, "160", BASE_TIME + timedelta(seconds=15))
    result = tracker(db_session, patient)
    confirmed_at = result.confirmed_at
    ingest(db_session, patient, "110", BASE_TIME - timedelta(minutes=1))
    db_session.refresh(result)
    assert result.last_event_id == current.event_id
    assert result.confirmed_at == confirmed_at


def test_equal_timestamp_cannot_reduce_severity(db_session, patient):
    ingest(db_session, patient, "165", BASE_TIME)
    critical = ingest(db_session, patient, "160", BASE_TIME + timedelta(seconds=15))
    ingest(db_session, patient, "110", BASE_TIME + timedelta(seconds=15))
    ingest(db_session, patient, "78", BASE_TIME + timedelta(seconds=15))
    result = tracker(db_session, patient)
    assert result.active is True
    assert result.last_event_id == critical.event_id
    assert result.confirmed_at is not None


def test_delayed_stale_event_is_audit_only(db_session, patient):
    current = ingest(db_session, patient, "110", BASE_TIME)
    delayed = ingest(
        db_session,
        patient,
        "78",
        BASE_TIME - timedelta(hours=1),
        ValidationStatus.DELAYED_USABLE,
    )
    assert delayed.event_id is not None
    assert tracker(db_session, patient).last_event_id == current.event_id


def test_equivalent_offsets_compare_as_same_instant(db_session, patient):
    critical = ingest(db_session, patient, "165", "2026-07-17T05:00:00Z")
    ingest(db_session, patient, "78", "2026-07-17T13:00:00+08:00")
    assert tracker(db_session, patient).last_event_id == critical.event_id
