from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.households.model import Household
from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
)
from app.reminders.lifecycle import process_reminder_lifecycle
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate
from app.users.model import User


pytestmark = pytest.mark.integration
NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def add_occurrence(
    db_session,
    patient,
    *,
    status,
    scheduled_at,
    due_at,
    title="Lifecycle reminder",
):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    template = ReminderTemplate(
        patient_id=patient.patient_id,
        created_by_user_id=owner.user_id,
        title=title,
        category=ReminderCategory.MEDICATION,
        priority=ReminderPriority.NORMAL,
        start_date=date(2026, 9, 16),
        start_time=time(8),
        timezone="Asia/Manila",
        missed_after_minutes=30,
    )
    db_session.add(template)
    db_session.flush()
    occurrence = ReminderOccurrence(
        reminder_template_id=template.reminder_template_id,
        scheduled_at=scheduled_at,
        due_at=due_at,
        status=status,
    )
    db_session.add(occurrence)
    db_session.flush()
    return occurrence


def test_processor_advances_states_audits_missed_and_is_idempotent(
    db_session, patient
):
    future = add_occurrence(
        db_session,
        patient,
        status=ReminderOccurrenceStatus.UPCOMING,
        scheduled_at=NOW + timedelta(minutes=1),
        due_at=NOW + timedelta(minutes=16),
        title="Future",
    )
    due = add_occurrence(
        db_session,
        patient,
        status=ReminderOccurrenceStatus.UPCOMING,
        scheduled_at=NOW - timedelta(minutes=5),
        due_at=NOW + timedelta(minutes=10),
        title="Due",
    )
    caught_up = add_occurrence(
        db_session,
        patient,
        status=ReminderOccurrenceStatus.UPCOMING,
        scheduled_at=NOW - timedelta(hours=2),
        due_at=NOW - timedelta(minutes=31),
        title="Caught up",
    )
    overdue = add_occurrence(
        db_session,
        patient,
        status=ReminderOccurrenceStatus.DUE,
        scheduled_at=NOW - timedelta(hours=2),
        due_at=NOW - timedelta(minutes=30),
        title="Overdue",
    )
    snoozed = add_occurrence(
        db_session,
        patient,
        status=ReminderOccurrenceStatus.SNOOZED,
        scheduled_at=NOW - timedelta(hours=2),
        due_at=NOW - timedelta(minutes=30),
        title="Snoozed",
    )
    completed = add_occurrence(
        db_session,
        patient,
        status=ReminderOccurrenceStatus.COMPLETED,
        scheduled_at=NOW - timedelta(hours=2),
        due_at=NOW - timedelta(hours=1),
        title="Completed",
    )
    db_session.commit()

    result = process_reminder_lifecycle(db_session, at=NOW)
    db_session.commit()

    assert (result.processed, result.marked_due, result.marked_missed) == (4, 1, 3)
    assert db_session.get(ReminderOccurrence, future.reminder_occurrence_id).status is ReminderOccurrenceStatus.UPCOMING
    assert db_session.get(ReminderOccurrence, due.reminder_occurrence_id).status is ReminderOccurrenceStatus.DUE
    assert db_session.get(ReminderOccurrence, caught_up.reminder_occurrence_id).status is ReminderOccurrenceStatus.MISSED
    assert db_session.get(ReminderOccurrence, overdue.reminder_occurrence_id).status is ReminderOccurrenceStatus.MISSED
    assert db_session.get(ReminderOccurrence, snoozed.reminder_occurrence_id).status is ReminderOccurrenceStatus.MISSED
    assert db_session.get(ReminderOccurrence, completed.reminder_occurrence_id).status is ReminderOccurrenceStatus.COMPLETED

    actions = list(db_session.scalars(select(ReminderAction)))
    assert len(actions) == 3
    assert all(action.action_type is ReminderActionType.MARK_MISSED for action in actions)
    assert all(action.performed_by_user_id is None for action in actions)
    assert all(action.action_metadata == {"source": "reminder_lifecycle", "automated": True} for action in actions)

    replay = process_reminder_lifecycle(db_session, at=NOW)
    db_session.commit()
    assert replay.processed == 0
    assert db_session.scalar(select(func.count()).select_from(ReminderAction)) == 3


def test_processor_skips_rows_locked_by_another_worker(
    integration_engine, db_session, patient
):
    first = add_occurrence(
        db_session,
        patient,
        status=ReminderOccurrenceStatus.UPCOMING,
        scheduled_at=NOW - timedelta(minutes=2),
        due_at=NOW + timedelta(minutes=10),
        title="First",
    )
    second = add_occurrence(
        db_session,
        patient,
        status=ReminderOccurrenceStatus.UPCOMING,
        scheduled_at=NOW - timedelta(minutes=1),
        due_at=NOW + timedelta(minutes=10),
        title="Second",
    )
    db_session.commit()
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    locking_session = factory()
    worker_session = factory()
    try:
        locking_session.execute(
            select(ReminderOccurrence)
            .where(
                ReminderOccurrence.reminder_occurrence_id
                == first.reminder_occurrence_id
            )
            .with_for_update()
        ).scalar_one()

        result = process_reminder_lifecycle(worker_session, at=NOW)
        worker_session.commit()
        assert (result.processed, result.marked_due) == (1, 1)
    finally:
        locking_session.rollback()
        locking_session.close()
        worker_session.close()

    db_session.expire_all()
    assert db_session.get(ReminderOccurrence, first.reminder_occurrence_id).status is ReminderOccurrenceStatus.UPCOMING
    assert db_session.get(ReminderOccurrence, second.reminder_occurrence_id).status is ReminderOccurrenceStatus.DUE


@pytest.mark.parametrize("limit", [0, 501])
def test_processor_rejects_invalid_batch_limits(db_session, limit):
    with pytest.raises(ValueError, match="limit must be between"):
        process_reminder_lifecycle(db_session, at=NOW, limit=limit)
