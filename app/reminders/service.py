from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.reminders.access import resolve_list_patient_id, visible_patient_ids
from app.core.time import utc_now
from app.households.model import Household, HouseholdStatus
from app.patients.model import ElderlyPatient
from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderOccurrenceStatus,
)
from app.reminders.errors import (
    ReminderAccessForbiddenError,
    ReminderActionConflictError,
    ReminderNotFoundError,
    ReminderQueryValidationError,
)
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate
from app.users.model import User, UserRole


ReminderRow = tuple[ReminderOccurrence, ReminderTemplate]
ReminderActionResult = tuple[ReminderOccurrence, ReminderTemplate, ReminderAction, bool]


def validate_reminder_time_range(
    from_at: datetime | None,
    before_at: datetime | None,
) -> None:
    for name, value in (("from_at", from_at), ("before_at", before_at)):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ReminderQueryValidationError(f"{name} must include timezone information.")
    if from_at is not None and before_at is not None and before_at <= from_at:
        raise ReminderQueryValidationError("before_at must be greater than from_at.")


def _reminder_filters(
    *,
    patient_id: UUID,
    from_at: datetime | None,
    before_at: datetime | None,
    statuses: list[ReminderOccurrenceStatus] | None,
    categories: list[ReminderCategory] | None,
) -> list:
    filters = [ReminderTemplate.patient_id == patient_id]
    if from_at is not None:
        filters.append(ReminderOccurrence.scheduled_at >= from_at)
    if before_at is not None:
        filters.append(ReminderOccurrence.scheduled_at < before_at)
    if statuses:
        filters.append(ReminderOccurrence.status.in_(statuses))
    if categories:
        filters.append(ReminderTemplate.category.in_(categories))
    return filters


def list_reminder_occurrences(
    db: Session,
    *,
    actor: User,
    patient_id: UUID | None,
    from_at: datetime | None,
    before_at: datetime | None,
    statuses: list[ReminderOccurrenceStatus] | None,
    categories: list[ReminderCategory] | None,
    limit: int,
    offset: int,
) -> tuple[list[ReminderRow], int]:
    validate_reminder_time_range(from_at, before_at)
    scoped_patient_id = resolve_list_patient_id(
        db, actor=actor, patient_id=patient_id
    )
    filters = _reminder_filters(
        patient_id=scoped_patient_id,
        from_at=from_at,
        before_at=before_at,
        statuses=statuses,
        categories=categories,
    )
    base = select(ReminderOccurrence, ReminderTemplate).join(
        ReminderTemplate,
        ReminderOccurrence.reminder_template_id
        == ReminderTemplate.reminder_template_id,
    )
    total = db.scalar(
        select(func.count(ReminderOccurrence.reminder_occurrence_id))
        .select_from(ReminderOccurrence)
        .join(
            ReminderTemplate,
            ReminderOccurrence.reminder_template_id
            == ReminderTemplate.reminder_template_id,
        )
        .where(*filters)
    ) or 0
    return list(
        db.execute(
            base.where(*filters)
            .order_by(
                ReminderOccurrence.scheduled_at.asc(),
                ReminderOccurrence.reminder_occurrence_id.asc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()
    ), total


def get_reminder_occurrence(
    db: Session,
    *,
    actor: User,
    occurrence_id: UUID,
) -> ReminderRow:
    row = db.execute(
        select(ReminderOccurrence, ReminderTemplate)
        .join(
            ReminderTemplate,
            ReminderOccurrence.reminder_template_id
            == ReminderTemplate.reminder_template_id,
        )
        .where(
            ReminderOccurrence.reminder_occurrence_id == occurrence_id,
            ReminderTemplate.patient_id.in_(visible_patient_ids(actor)),
        )
    ).one_or_none()
    if row is None:
        raise ReminderNotFoundError("Reminder occurrence not found.")
    return row


def reminder_occurrence_payload(
    occurrence: ReminderOccurrence,
    template: ReminderTemplate,
) -> dict:
    return {
        "reminder_occurrence_id": occurrence.reminder_occurrence_id,
        "reminder_template_id": occurrence.reminder_template_id,
        "patient_id": template.patient_id,
        "title": template.title,
        "instructions": template.instructions,
        "category": template.category,
        "priority": template.priority,
        "scheduled_at": occurrence.scheduled_at,
        "due_at": occurrence.due_at,
        "status": occurrence.status,
        "snooze_allowed": template.snooze_allowed,
        "default_snooze_minutes": template.default_snooze_minutes,
        "missed_after_minutes": template.missed_after_minutes,
    }


def reminder_action_payload(action: ReminderAction) -> dict:
    return {
        "reminder_action_id": action.reminder_action_id,
        "client_action_id": action.client_action_id,
        "reminder_occurrence_id": action.reminder_occurrence_id,
        "performed_by_user_id": action.performed_by_user_id,
        "action_type": action.action_type,
        "action_note": action.action_note,
        "previous_status": action.previous_status,
        "new_status": action.new_status,
        "new_due_at": (
            action.new_due_at.astimezone(timezone.utc)
            if action.new_due_at is not None
            else None
        ),
        "metadata": action.action_metadata or {},
        "performed_at": action.performed_at.astimezone(timezone.utc),
    }


def _locked_patient_occurrence(
    db: Session, *, actor: User, occurrence_id: UUID
) -> ReminderRow:
    if actor.role is not UserRole.ELDERLY_PATIENT:
        raise ReminderAccessForbiddenError("Only patients may perform reminder actions.")
    row = db.execute(
        select(ReminderOccurrence, ReminderTemplate)
        .join(
            ReminderTemplate,
            ReminderOccurrence.reminder_template_id
            == ReminderTemplate.reminder_template_id,
        )
        .join(ElderlyPatient, ElderlyPatient.patient_id == ReminderTemplate.patient_id)
        .join(Household, Household.household_id == ElderlyPatient.household_id)
        .where(
            ReminderOccurrence.reminder_occurrence_id == occurrence_id,
            ElderlyPatient.user_id == actor.user_id,
            ElderlyPatient.archived_at.is_(None),
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
        )
        .with_for_update(of=ReminderOccurrence)
    ).one_or_none()
    if row is None:
        raise ReminderNotFoundError("Reminder occurrence not found.")
    return row


def _same_action(
    action: ReminderAction,
    *,
    actor: User,
    occurrence_id: UUID,
    action_type: ReminderActionType,
    note: str | None,
    snooze_minutes: int | None,
) -> bool:
    if (
        action.performed_by_user_id != actor.user_id
        or action.reminder_occurrence_id != occurrence_id
        or action.action_type is not action_type
        or action.action_note != note
    ):
        return False
    if action_type is ReminderActionType.SNOOZE:
        return (action.action_metadata or {}).get("snooze_minutes") == snooze_minutes
    return True


def _existing_action(
    db: Session, *, client_action_id: UUID
) -> ReminderAction | None:
    return db.scalar(
        select(ReminderAction).where(ReminderAction.client_action_id == client_action_id)
    )


def _replay_or_conflict(
    action: ReminderAction | None,
    *,
    actor: User,
    occurrence: ReminderOccurrence,
    action_type: ReminderActionType,
    note: str | None,
    snooze_minutes: int | None,
) -> ReminderAction | None:
    if action is None:
        return None
    if _same_action(
        action,
        actor=actor,
        occurrence_id=occurrence.reminder_occurrence_id,
        action_type=action_type,
        note=note,
        snooze_minutes=snooze_minutes,
    ):
        return action
    raise ReminderActionConflictError("client_action_id was already used for another action.")


def _insert_action_safely(
    db: Session,
    *,
    action: ReminderAction,
    actor: User,
    occurrence: ReminderOccurrence,
    action_type: ReminderActionType,
    note: str | None,
    snooze_minutes: int | None,
) -> ReminderAction | None:
    try:
        with db.begin_nested():
            db.add(action)
            db.flush()
        return None
    except IntegrityError:
        existing = _existing_action(db, client_action_id=action.client_action_id)
        replay = _replay_or_conflict(
            existing,
            actor=actor,
            occurrence=occurrence,
            action_type=action_type,
            note=note,
            snooze_minutes=snooze_minutes,
        )
        if replay is not None:
            db.refresh(occurrence)
        return replay


def complete_reminder(
    db: Session,
    *,
    actor: User,
    occurrence_id: UUID,
    client_action_id: UUID,
    note: str | None,
    at: datetime | None = None,
) -> ReminderActionResult:
    occurrence, template = _locked_patient_occurrence(
        db, actor=actor, occurrence_id=occurrence_id
    )
    replay = _replay_or_conflict(
        _existing_action(db, client_action_id=client_action_id),
        actor=actor,
        occurrence=occurrence,
        action_type=ReminderActionType.MARK_COMPLETED,
        note=note,
        snooze_minutes=None,
    )
    if replay is not None:
        return occurrence, template, replay, True
    if occurrence.status in {
        ReminderOccurrenceStatus.COMPLETED,
        ReminderOccurrenceStatus.COMPLETED_LATE,
        ReminderOccurrenceStatus.CANCELED,
    }:
        raise ReminderActionConflictError("Reminder occurrence cannot be completed.")
    action_time = at or utc_now()
    previous_status = occurrence.status
    new_status = (
        ReminderOccurrenceStatus.COMPLETED_LATE
        if previous_status is ReminderOccurrenceStatus.MISSED or action_time > occurrence.due_at
        else ReminderOccurrenceStatus.COMPLETED
    )
    action = ReminderAction(
        client_action_id=client_action_id,
        reminder_occurrence_id=occurrence.reminder_occurrence_id,
        performed_by_user_id=actor.user_id,
        action_type=ReminderActionType.MARK_COMPLETED,
        action_note=note,
        previous_status=previous_status,
        new_status=new_status,
        new_due_at=None,
        action_metadata={},
        performed_at=action_time,
    )
    replay = _insert_action_safely(
        db, action=action, actor=actor, occurrence=occurrence,
        action_type=ReminderActionType.MARK_COMPLETED, note=note, snooze_minutes=None,
    )
    if replay is not None:
        return occurrence, template, replay, True
    occurrence.status = new_status
    occurrence.updated_at = action_time
    return occurrence, template, action, False


def snooze_reminder(
    db: Session,
    *,
    actor: User,
    occurrence_id: UUID,
    client_action_id: UUID,
    snooze_minutes: int | None,
    note: str | None,
    at: datetime | None = None,
) -> ReminderActionResult:
    occurrence, template = _locked_patient_occurrence(
        db, actor=actor, occurrence_id=occurrence_id
    )
    effective_minutes = (
        template.default_snooze_minutes if snooze_minutes is None else snooze_minutes
    )
    replay = _replay_or_conflict(
        _existing_action(db, client_action_id=client_action_id),
        actor=actor, occurrence=occurrence, action_type=ReminderActionType.SNOOZE,
        note=note, snooze_minutes=effective_minutes,
    )
    if replay is not None:
        return occurrence, template, replay, True
    if not template.snooze_allowed:
        raise ReminderActionConflictError("Snooze is not allowed for this reminder.")
    if effective_minutes is None or not 1 <= effective_minutes <= 1440:
        raise ReminderActionConflictError("Reminder snooze configuration is invalid.")
    if occurrence.status not in {
        ReminderOccurrenceStatus.UPCOMING,
        ReminderOccurrenceStatus.DUE,
        ReminderOccurrenceStatus.SNOOZED,
    }:
        raise ReminderActionConflictError("Reminder occurrence cannot be snoozed.")
    action_time = at or utc_now()
    previous_status = occurrence.status
    previous_due_at = occurrence.due_at
    new_due_at = action_time + timedelta(minutes=effective_minutes)
    action = ReminderAction(
        client_action_id=client_action_id,
        reminder_occurrence_id=occurrence.reminder_occurrence_id,
        performed_by_user_id=actor.user_id,
        action_type=ReminderActionType.SNOOZE,
        action_note=note,
        previous_status=previous_status,
        new_status=ReminderOccurrenceStatus.SNOOZED,
        new_due_at=new_due_at,
        action_metadata={
            "snooze_minutes": effective_minutes,
            "previous_due_at": previous_due_at.astimezone(timezone.utc).isoformat(),
        },
        performed_at=action_time,
    )
    replay = _insert_action_safely(
        db, action=action, actor=actor, occurrence=occurrence,
        action_type=ReminderActionType.SNOOZE, note=note,
        snooze_minutes=effective_minutes,
    )
    if replay is not None:
        return occurrence, template, replay, True
    occurrence.status = ReminderOccurrenceStatus.SNOOZED
    occurrence.due_at = new_due_at
    occurrence.updated_at = action_time
    return occurrence, template, action, False
