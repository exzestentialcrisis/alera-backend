from datetime import datetime, timedelta, timezone

import pytest

from app.reminders.enums import ReminderOccurrenceStatus
from app.reminders.lifecycle import reminder_lifecycle_target


NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def target(status, *, scheduled_offset, due_offset, missed_after=30):
    return reminder_lifecycle_target(
        status=status,
        scheduled_at=NOW + timedelta(minutes=scheduled_offset),
        due_at=NOW + timedelta(minutes=due_offset),
        missed_after_minutes=missed_after,
        at=NOW,
    )


def test_upcoming_becomes_due_at_scheduled_time():
    assert target(
        ReminderOccurrenceStatus.UPCOMING,
        scheduled_offset=0,
        due_offset=15,
    ) is ReminderOccurrenceStatus.DUE


def test_upcoming_becomes_due_within_one_minute_lead_window():
    assert target(
        ReminderOccurrenceStatus.UPCOMING,
        scheduled_offset=1,
        due_offset=16,
    ) is ReminderOccurrenceStatus.DUE


def test_upcoming_stays_upcoming_outside_one_minute_lead_window():
    assert target(
        ReminderOccurrenceStatus.UPCOMING,
        scheduled_offset=1.01,
        due_offset=16,
    ) is ReminderOccurrenceStatus.UPCOMING


def test_overdue_upcoming_catches_up_directly_to_missed():
    assert target(
        ReminderOccurrenceStatus.UPCOMING,
        scheduled_offset=-60,
        due_offset=-31,
    ) is ReminderOccurrenceStatus.MISSED


@pytest.mark.parametrize(
    "status",
    [ReminderOccurrenceStatus.DUE, ReminderOccurrenceStatus.SNOOZED],
)
def test_due_and_snoozed_become_missed_after_deadline(status):
    assert target(
        status,
        scheduled_offset=-60,
        due_offset=-30,
    ) is ReminderOccurrenceStatus.MISSED


@pytest.mark.parametrize(
    "status",
    [
        ReminderOccurrenceStatus.COMPLETED,
        ReminderOccurrenceStatus.COMPLETED_LATE,
        ReminderOccurrenceStatus.CANCELED,
        ReminderOccurrenceStatus.MISSED,
    ],
)
def test_terminal_states_never_change(status):
    assert target(
        status,
        scheduled_offset=-120,
        due_offset=-120,
    ) is status


def test_lifecycle_time_must_be_timezone_aware():
    with pytest.raises(ValueError, match="at must include timezone"):
        reminder_lifecycle_target(
            status=ReminderOccurrenceStatus.UPCOMING,
            scheduled_at=NOW,
            due_at=NOW,
            missed_after_minutes=30,
            at=NOW.replace(tzinfo=None),
        )
