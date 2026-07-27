from datetime import datetime, timedelta, timezone

from app.condition_trackers.model import ConditionTracker
from app.condition_trackers.service import (
    ConditionTrackerUpdateResult,
    HR_CONTINUITY_GAP,
    SPO2_CONTINUITY_GAP,
    TrackerUpdateIgnoredReason,
)


def test_tracker_result_exposes_occurrence_state():
    previous = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
    result = ConditionTrackerUpdateResult(
        applied=True,
        occurrence_started=True,
        previous_started_at=previous,
    )

    assert result.occurrence_started is True
    assert result.previous_started_at == previous


def test_tracker_result_can_report_ineligible_measurement():
    result = ConditionTrackerUpdateResult(
        applied=False,
        ignored_reason=TrackerUpdateIgnoredReason.INELIGIBLE_VALIDATION_STATUS,
    )

    assert result.applied is False
    assert (
        result.ignored_reason
        is TrackerUpdateIgnoredReason.INELIGIBLE_VALIDATION_STATUS
    )


def test_tracker_continuity_contracts_and_counter_defaults():
    column = ConditionTracker.__table__.c.consecutive_event_count

    assert HR_CONTINUITY_GAP == timedelta(seconds=90)
    assert SPO2_CONTINUITY_GAP == timedelta(minutes=5)
    assert column.default.arg == 0
    assert str(column.server_default.arg) == "0"
    assert column.nullable is False
