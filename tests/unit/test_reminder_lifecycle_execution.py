from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.reminders.execution import execute_reminder_lifecycle
from app.reminders.lifecycle import ReminderLifecycleResult


NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def test_execution_drains_multiple_batches_and_commits_each(monkeypatch):
    db = Mock()
    results = iter(
        [
            ReminderLifecycleResult(processed=2, marked_due=1, marked_missed=1),
            ReminderLifecycleResult(processed=1, marked_due=1, marked_missed=0),
        ]
    )
    monkeypatch.setattr(
        "app.reminders.execution.process_reminder_lifecycle",
        lambda *_args, **_kwargs: next(results),
    )

    result = execute_reminder_lifecycle(
        db,
        at=NOW,
        batch_size=2,
        max_batches=5,
    )

    assert result.processed == 3
    assert result.marked_due == 2
    assert result.marked_missed == 1
    assert result.batches == 2
    assert result.limit_reached is False
    assert db.commit.call_count == 2
    db.rollback.assert_not_called()


def test_execution_reports_when_run_limit_is_reached(monkeypatch):
    db = Mock()
    monkeypatch.setattr(
        "app.reminders.execution.process_reminder_lifecycle",
        lambda *_args, **_kwargs: ReminderLifecycleResult(
            processed=2,
            marked_due=2,
            marked_missed=0,
        ),
    )

    result = execute_reminder_lifecycle(
        db,
        at=NOW,
        batch_size=2,
        max_batches=2,
    )

    assert result.processed == 4
    assert result.batches == 2
    assert result.limit_reached is True
    assert db.commit.call_count == 2


def test_empty_execution_releases_transaction_without_commit(monkeypatch):
    db = Mock()
    monkeypatch.setattr(
        "app.reminders.execution.process_reminder_lifecycle",
        lambda *_args, **_kwargs: ReminderLifecycleResult(
            processed=0,
            marked_due=0,
            marked_missed=0,
        ),
    )

    result = execute_reminder_lifecycle(db, at=NOW)

    assert result.processed == 0
    assert result.batches == 0
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()


def test_failed_batch_is_rolled_back(monkeypatch):
    db = Mock()

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic lifecycle failure")

    monkeypatch.setattr(
        "app.reminders.execution.process_reminder_lifecycle",
        fail,
    )

    with pytest.raises(RuntimeError, match="synthetic lifecycle failure"):
        execute_reminder_lifecycle(db, at=NOW)

    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    ("batch_size", "max_batches", "message"),
    [
        (0, 1, "batch_size"),
        (501, 1, "batch_size"),
        (1, 0, "max_batches"),
        (1, 21, "max_batches"),
    ],
)
def test_execution_rejects_unsafe_limits(batch_size, max_batches, message):
    with pytest.raises(ValueError, match=message):
        execute_reminder_lifecycle(
            Mock(),
            at=NOW,
            batch_size=batch_size,
            max_batches=max_batches,
        )
