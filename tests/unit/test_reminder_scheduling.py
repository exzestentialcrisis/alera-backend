from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from app.reminders.scheduling import (
    ReminderScheduleValidationError,
    normalize_schedule_rule,
    occurrence_times_for_window,
    parse_schedule_rule,
)


def template(**overrides):
    values = {
        "start_date": date(2026, 9, 16),
        "start_time": time(8),
        "timezone": "Asia/Manila",
        "schedule_rule": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        (None, None),
        ("FREQ=DAILY", "FREQ=DAILY"),
        ("rrule:freq=daily;interval=2", "FREQ=DAILY;INTERVAL=2"),
        (
            "FREQ=WEEKLY;BYDAY=FR,MO,WE",
            "FREQ=WEEKLY;BYDAY=MO,WE,FR",
        ),
        (
            "FREQ=WEEKLY;INTERVAL=2;BYDAY=TH,TU",
            "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU,TH",
        ),
    ],
)
def test_schedule_rule_normalization(raw, canonical):
    assert normalize_schedule_rule(raw) == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "DAILY",
        "FREQ=MONTHLY",
        "FREQ=DAILY;COUNT=3",
        "FREQ=DAILY;INTERVAL=0",
        "FREQ=DAILY;INTERVAL=nope",
        "FREQ=DAILY;BYDAY=MO",
        "FREQ=WEEKLY;BYDAY=MO,MO",
        "FREQ=WEEKLY;BYDAY=XX",
        "FREQ=DAILY;FREQ=WEEKLY",
    ],
)
def test_invalid_schedule_rules_are_rejected(raw):
    with pytest.raises(ReminderScheduleValidationError):
        parse_schedule_rule(raw)


def test_one_time_schedule_converts_local_time_and_uses_half_open_window():
    item = template()
    assert occurrence_times_for_window(
        item,
        window_start=utc(2026, 9, 16),
        window_end=utc(2026, 9, 17),
    ) == [utc(2026, 9, 16)]
    assert occurrence_times_for_window(
        item,
        window_start=utc(2026, 9, 16, 0, 1),
        window_end=utc(2026, 9, 17),
    ) == []


def test_daily_interval_schedule():
    item = template(schedule_rule="FREQ=DAILY;INTERVAL=2")
    assert occurrence_times_for_window(
        item,
        window_start=utc(2026, 9, 16),
        window_end=utc(2026, 9, 22),
    ) == [
        utc(2026, 9, 16),
        utc(2026, 9, 18),
        utc(2026, 9, 20),
    ]


def test_weekly_selected_days_and_interval_are_calendar_week_based():
    item = template(schedule_rule="FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,FR")
    assert occurrence_times_for_window(
        item,
        window_start=utc(2026, 9, 16),
        window_end=utc(2026, 10, 1),
    ) == [
        utc(2026, 9, 16),
        utc(2026, 9, 18),
        utc(2026, 9, 28),
        utc(2026, 9, 30),
    ]


def test_weekly_without_byday_uses_start_weekday():
    item = template(schedule_rule="FREQ=WEEKLY")
    assert occurrence_times_for_window(
        item,
        window_start=utc(2026, 9, 16),
        window_end=utc(2026, 10, 1),
    ) == [utc(2026, 9, 16), utc(2026, 9, 23), utc(2026, 9, 30)]


def test_dst_gap_is_rejected():
    item = template(
        start_date=date(2026, 3, 8),
        start_time=time(2, 30),
        timezone="America/New_York",
    )
    with pytest.raises(ReminderScheduleValidationError, match="does not exist"):
        occurrence_times_for_window(
            item,
            window_start=utc(2026, 3, 8),
            window_end=utc(2026, 3, 9),
        )


def test_dst_fold_chooses_earlier_instant():
    item = template(
        start_date=date(2026, 11, 1),
        start_time=time(1, 30),
        timezone="America/New_York",
    )
    assert occurrence_times_for_window(
        item,
        window_start=utc(2026, 11, 1),
        window_end=utc(2026, 11, 2),
    ) == [utc(2026, 11, 1, 5, 30)]


def test_windows_must_be_aware_and_ordered():
    item = template()
    with pytest.raises(ReminderScheduleValidationError, match="window_start"):
        occurrence_times_for_window(
            item,
            window_start=datetime(2026, 9, 16),
            window_end=utc(2026, 9, 17),
        )
    with pytest.raises(ReminderScheduleValidationError, match="greater"):
        occurrence_times_for_window(
            item,
            window_start=utc(2026, 9, 17),
            window_end=utc(2026, 9, 16),
        )
