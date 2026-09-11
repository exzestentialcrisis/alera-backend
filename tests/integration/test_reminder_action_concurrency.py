from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.households.model import Household
from app.household_access.model import CaregiverPatientAssignment
from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
)
from app.reminders.errors import ReminderActionConflictError
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate
from app.reminders.service import (
    cancel_reminder,
    complete_reminder,
    complete_reminder_on_behalf,
    record_caregiver_reminder_action,
    snooze_reminder,
)
from app.users.model import User, UserRole


pytestmark = pytest.mark.integration
ACTION_TIME = datetime(2026, 9, 11, 8, tzinfo=timezone.utc)


def make_occurrence(db_session, patient):
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    template = ReminderTemplate(
        patient_id=patient.patient_id,
        created_by_user_id=owner.user_id,
        title="Concurrent reminder",
        category=ReminderCategory.MEDICATION,
        priority=ReminderPriority.NORMAL,
        start_date=ACTION_TIME.date(),
        start_time=ACTION_TIME.time(),
        default_snooze_minutes=10,
    )
    db_session.add(template)
    db_session.flush()
    occurrence = ReminderOccurrence(
        reminder_template_id=template.reminder_template_id,
        scheduled_at=ACTION_TIME,
        due_at=ACTION_TIME + timedelta(days=1),
        status=ReminderOccurrenceStatus.UPCOMING,
    )
    db_session.add(occurrence)
    db_session.commit()
    return occurrence.reminder_occurrence_id


def run_concurrently(integration_engine, actor_id, occurrence_id, action_ids, operation):
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    barrier = Barrier(len(action_ids))

    def worker(action_id):
        session = factory()
        try:
            actor = session.get(User, actor_id)
            barrier.wait()
            result = operation(session, actor, occurrence_id, action_id)
            session.commit()
            return "success", result[3]
        except ReminderActionConflictError:
            session.rollback()
            return "conflict", None
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=len(action_ids)) as executor:
        return list(executor.map(worker, action_ids))


def test_concurrent_same_complete_is_idempotent(integration_engine, db_session, patient):
    occurrence_id = make_occurrence(db_session, patient)
    action_id = uuid4()
    results = run_concurrently(
        integration_engine,
        patient.user_id,
        occurrence_id,
        [action_id, action_id],
        lambda session, actor, occurrence, client_action: complete_reminder(
            session,
            actor=actor,
            occurrence_id=occurrence,
            client_action_id=client_action,
            note=None,
            at=ACTION_TIME,
        ),
    )
    assert sorted(results) == [("success", False), ("success", True)]
    assert db_session.scalar(
        select(func.count()).select_from(ReminderAction).where(
            ReminderAction.reminder_occurrence_id == occurrence_id
        )
    ) == 1


def test_concurrent_different_complete_ids_conflict(integration_engine, db_session, patient):
    occurrence_id = make_occurrence(db_session, patient)
    results = run_concurrently(
        integration_engine,
        patient.user_id,
        occurrence_id,
        [uuid4(), uuid4()],
        lambda session, actor, occurrence, client_action: complete_reminder(
            session,
            actor=actor,
            occurrence_id=occurrence,
            client_action_id=client_action,
            note=None,
            at=ACTION_TIME,
        ),
    )
    assert sorted(result[0] for result in results) == ["conflict", "success"]


def test_concurrent_same_snooze_extends_once(integration_engine, db_session, patient):
    occurrence_id = make_occurrence(db_session, patient)
    action_id = uuid4()
    results = run_concurrently(
        integration_engine,
        patient.user_id,
        occurrence_id,
        [action_id, action_id],
        lambda session, actor, occurrence, client_action: snooze_reminder(
            session,
            actor=actor,
            occurrence_id=occurrence,
            client_action_id=client_action,
            snooze_minutes=15,
            note=None,
            at=ACTION_TIME,
        ),
    )
    assert sorted(results) == [("success", False), ("success", True)]
    occurrence = db_session.get(ReminderOccurrence, occurrence_id)
    assert occurrence.due_at == ACTION_TIME + timedelta(minutes=15)
    action = db_session.scalar(
        select(ReminderAction).where(
            ReminderAction.reminder_occurrence_id == occurrence_id,
            ReminderAction.action_type == ReminderActionType.SNOOZE,
        )
    )
    assert action.action_metadata["snooze_minutes"] == 15


def test_concurrent_missed_handled_allows_one_caregiver_action(
    integration_engine, db_session, patient
):
    occurrence_id = make_occurrence(db_session, patient)
    occurrence = db_session.get(ReminderOccurrence, occurrence_id)
    occurrence.status = ReminderOccurrenceStatus.MISSED
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    caregiver = User(full_name="Concurrent caregiver", role=UserRole.CAREGIVER)
    db_session.add(caregiver)
    db_session.flush()
    db_session.add(CaregiverPatientAssignment(
        caregiver_user_id=caregiver.user_id,
        patient_id=patient.patient_id,
        assigned_by_user_id=owner.user_id,
    ))
    db_session.commit()

    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    barrier = Barrier(2)

    def worker(actor_id, action_id):
        session = factory()
        try:
            actor = session.get(User, actor_id)
            barrier.wait()
            record_caregiver_reminder_action(
                session,
                actor=actor,
                occurrence_id=occurrence_id,
                client_action_id=action_id,
                action_type=ReminderActionType.MARK_MISSED_HANDLED,
                note=None,
                at=ACTION_TIME,
            )
            session.commit()
            return "success"
        except ReminderActionConflictError:
            session.rollback()
            return "conflict"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda values: worker(*values),
            [(owner.user_id, uuid4()), (caregiver.user_id, uuid4())],
        ))
    assert sorted(results) == ["conflict", "success"]


def test_caregiver_terminal_actions_are_serialized(integration_engine, db_session, patient):
    occurrence_id = make_occurrence(db_session, patient)
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    caregivers = [
        User(full_name=f"Concurrent caregiver {index}", role=UserRole.CAREGIVER)
        for index in range(2)
    ]
    db_session.add_all(caregivers)
    db_session.flush()
    db_session.add_all([
        CaregiverPatientAssignment(
            caregiver_user_id=caregiver.user_id,
            patient_id=patient.patient_id,
            assigned_by_user_id=owner.user_id,
        )
        for caregiver in caregivers
    ])
    db_session.commit()
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)
    barrier = Barrier(2)

    def worker(actor_id, action_id, operation):
        session = factory()
        try:
            actor = session.get(User, actor_id)
            barrier.wait()
            operation(session, actor, occurrence_id, action_id)
            session.commit()
            return "success"
        except ReminderActionConflictError:
            session.rollback()
            return "conflict"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda values: worker(*values),
            [
                (caregivers[0].user_id, uuid4(), lambda session, actor, occurrence, action_id: complete_reminder_on_behalf(session, actor=actor, occurrence_id=occurrence, client_action_id=action_id, note="completed", at=ACTION_TIME)),
                (caregivers[1].user_id, uuid4(), lambda session, actor, occurrence, action_id: cancel_reminder(session, actor=actor, occurrence_id=occurrence, client_action_id=action_id, note="canceled", at=ACTION_TIME)),
            ],
        ))
    assert sorted(results) == ["conflict", "success"]
    assert db_session.scalar(select(func.count()).select_from(ReminderAction).where(
        ReminderAction.reminder_occurrence_id == occurrence_id
    )) == 1
    assert db_session.get(ReminderOccurrence, occurrence_id).status in {
        ReminderOccurrenceStatus.COMPLETED,
        ReminderOccurrenceStatus.CANCELED,
    }
