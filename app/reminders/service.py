from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.reminders.access import resolve_list_patient_id, visible_patient_ids
from app.reminders.enums import ReminderCategory, ReminderOccurrenceStatus
from app.reminders.errors import ReminderNotFoundError, ReminderQueryValidationError
from app.reminders.model import ReminderOccurrence, ReminderTemplate
from app.users.model import User


ReminderRow = tuple[ReminderOccurrence, ReminderTemplate]


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
