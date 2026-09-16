from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.households.model import Household
from app.reminders.enums import (
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
    ReminderTemplateStatus,
)
from app.reminders.model import ReminderOccurrence, ReminderTemplate
from app.reminders.scheduling import (
    MATERIALIZATION_WINDOW_DAYS,
    ReminderScheduleValidationError,
    materialize_active_reminder_occurrences,
    materialize_reminder_occurrences,
)
from app.users.model import User


pytestmark = pytest.mark.integration
WINDOW_START = datetime(2026, 9, 16, tzinfo=timezone.utc)


def add_template(db_session, patient, **overrides):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    template = ReminderTemplate(
        patient_id=patient.patient_id,
        created_by_user_id=owner.user_id,
        title=overrides.pop("title", "Morning Medication"),
        category=overrides.pop("category", ReminderCategory.MEDICATION),
        priority=overrides.pop("priority", ReminderPriority.NORMAL),
        start_date=overrides.pop("start_date", date(2026, 9, 16)),
        start_time=overrides.pop("start_time", time(8)),
        timezone=overrides.pop("timezone", "Asia/Manila"),
        schedule_rule=overrides.pop("schedule_rule", "FREQ=DAILY"),
        due_after_minutes=overrides.pop("due_after_minutes", 15),
        status=overrides.pop("status", ReminderTemplateStatus.ACTIVE),
        **overrides,
    )
    db_session.add(template)
    db_session.flush()
    return template


def test_materialization_creates_utc_occurrences_and_is_duplicate_safe(
    db_session, patient
):
    template = add_template(db_session, patient)

    inserted = materialize_reminder_occurrences(
        db_session,
        template=template,
        window_start=WINDOW_START,
        window_days=5,
    )
    replayed = materialize_reminder_occurrences(
        db_session,
        template=template,
        window_start=WINDOW_START,
        window_days=5,
    )

    assert inserted == 5
    assert replayed == 0
    occurrences = list(
        db_session.scalars(
            select(ReminderOccurrence)
            .where(
                ReminderOccurrence.reminder_template_id
                == template.reminder_template_id
            )
            .order_by(ReminderOccurrence.scheduled_at)
        )
    )
    assert [item.scheduled_at for item in occurrences] == [
        WINDOW_START + timedelta(days=offset) for offset in range(5)
    ]
    assert all(
        item.due_at == item.scheduled_at + timedelta(minutes=15)
        for item in occurrences
    )
    assert all(
        item.status is ReminderOccurrenceStatus.UPCOMING for item in occurrences
    )


def test_materialize_all_processes_only_active_templates(db_session, patient):
    active = add_template(
        db_session,
        patient,
        title="Active",
        schedule_rule=None,
    )
    disabled = add_template(
        db_session,
        patient,
        title="Disabled",
        schedule_rule=None,
        status=ReminderTemplateStatus.DISABLED,
    )

    inserted = materialize_active_reminder_occurrences(
        db_session,
        window_start=WINDOW_START,
        window_days=2,
    )

    assert inserted == 1
    counts = dict(
        db_session.execute(
            select(
                ReminderOccurrence.reminder_template_id,
                func.count(ReminderOccurrence.reminder_occurrence_id),
            ).group_by(ReminderOccurrence.reminder_template_id)
        ).all()
    )
    assert counts.get(active.reminder_template_id) == 1
    assert counts.get(disabled.reminder_template_id, 0) == 0


def test_single_inactive_template_is_a_noop(db_session, patient):
    template = add_template(
        db_session,
        patient,
        status=ReminderTemplateStatus.ARCHIVED,
    )
    assert materialize_reminder_occurrences(
        db_session,
        template=template,
        window_start=WINDOW_START,
    ) == 0


@pytest.mark.parametrize("window_days", [0, MATERIALIZATION_WINDOW_DAYS + 1])
def test_materialization_rejects_windows_outside_contract(
    db_session, patient, window_days
):
    template = add_template(db_session, patient)
    with pytest.raises(ReminderScheduleValidationError, match="window_days"):
        materialize_reminder_occurrences(
            db_session,
            template=template,
            window_start=WINDOW_START,
            window_days=window_days,
        )


def test_invalid_saved_rule_fails_without_partial_inserts(db_session, patient):
    template = add_template(
        db_session,
        patient,
        schedule_rule="FREQ=MONTHLY",
    )
    with pytest.raises(ReminderScheduleValidationError, match="DAILY or WEEKLY"):
        materialize_reminder_occurrences(
            db_session,
            template=template,
            window_start=WINDOW_START,
            window_days=2,
        )
    count = db_session.scalar(
        select(func.count(ReminderOccurrence.reminder_occurrence_id))
    )
    assert count == 0
