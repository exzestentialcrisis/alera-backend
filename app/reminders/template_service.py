from uuid import UUID

from datetime import timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household, HouseholdStatus
from app.patients.model import ElderlyPatient
from app.reminders.enums import (
    ReminderActionType,
    ReminderOccurrenceStatus,
    ReminderTemplateStatus,
)
from app.reminders.errors import (
    ReminderAccessForbiddenError,
    ReminderActionConflictError,
    ReminderNotFoundError,
    ReminderQueryValidationError,
)
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate
from app.reminders.scheduling import (
    MATERIALIZATION_WINDOW_DAYS,
    materialize_reminder_occurrences,
    occurrence_times_for_window,
)
from app.reminders.template_schema import (
    ReminderTemplateCreate,
    ReminderTemplateUpdate,
)
from app.users.model import User, UserRole


def _require_caregiver(actor: User) -> None:
    if actor.role is not UserRole.CAREGIVER:
        raise ReminderAccessForbiddenError(
            "Only assigned caregivers may manage reminder templates."
        )


def _assigned_patient_ids(actor: User):
    return (
        select(ElderlyPatient.patient_id)
        .join(Household, Household.household_id == ElderlyPatient.household_id)
        .join(
            CaregiverPatientAssignment,
            CaregiverPatientAssignment.patient_id == ElderlyPatient.patient_id,
        )
        .where(
            CaregiverPatientAssignment.caregiver_user_id == actor.user_id,
            CaregiverPatientAssignment.unassigned_at.is_(None),
            ElderlyPatient.archived_at.is_(None),
            Household.household_status == HouseholdStatus.ACTIVE,
            Household.archived_at.is_(None),
        )
    )


def _require_assigned_patient(
    db: Session, *, actor: User, patient_id: UUID
) -> None:
    _require_caregiver(actor)
    if db.scalar(
        select(ElderlyPatient.patient_id).where(
            ElderlyPatient.patient_id == patient_id,
            ElderlyPatient.patient_id.in_(_assigned_patient_ids(actor)),
        )
    ) is None:
        raise ReminderNotFoundError("Reminder patient not found.")


def reminder_template_payload(template: ReminderTemplate) -> dict:
    return {
        "reminder_template_id": template.reminder_template_id,
        "patient_id": template.patient_id,
        "created_by_user_id": template.created_by_user_id,
        "title": template.title,
        "category": template.category,
        "instructions": template.instructions,
        "priority": template.priority,
        "start_date": template.start_date,
        "start_time": template.start_time,
        "timezone": template.timezone,
        "schedule_rule": template.schedule_rule,
        "due_after_minutes": template.due_after_minutes,
        "snooze_allowed": template.snooze_allowed,
        "default_snooze_minutes": template.default_snooze_minutes,
        "missed_after_minutes": template.missed_after_minutes,
        "notification_channels": template.notification_channels,
        "status": template.status,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
        "archived_at": template.archived_at,
    }


def create_reminder_template(
    db: Session,
    *,
    actor: User,
    payload: ReminderTemplateCreate,
) -> ReminderTemplate:
    _require_assigned_patient(db, actor=actor, patient_id=payload.patient_id)
    template = ReminderTemplate(
        patient_id=payload.patient_id,
        created_by_user_id=actor.user_id,
        title=payload.title,
        category=payload.category,
        instructions=payload.instructions,
        priority=payload.priority,
        start_date=payload.start_date,
        start_time=payload.start_time,
        timezone=payload.timezone,
        schedule_rule=payload.schedule_rule,
        due_after_minutes=payload.due_after_minutes,
        snooze_allowed=payload.snooze_allowed,
        default_snooze_minutes=payload.default_snooze_minutes,
        missed_after_minutes=payload.missed_after_minutes,
        notification_channels=payload.notification_channels,
        status=ReminderTemplateStatus.ACTIVE,
    )
    db.add(template)
    db.flush()
    materialize_reminder_occurrences(
        db, template=template, window_start=utc_now()
    )
    return template


_SCHEDULE_FIELDS = {
    "start_date",
    "start_time",
    "timezone",
    "schedule_rule",
    "due_after_minutes",
}


def _cancel_future_occurrences(
    db: Session,
    *,
    template: ReminderTemplate,
    actor: User,
    now,
    reason: str,
) -> int:
    """Cancel unacted-on future occurrences and retain an audit trail."""
    occurrences = list(
        db.scalars(
            select(ReminderOccurrence)
            .where(
                ReminderOccurrence.reminder_template_id
                == template.reminder_template_id,
                ReminderOccurrence.status == ReminderOccurrenceStatus.UPCOMING,
                ReminderOccurrence.scheduled_at >= now,
            )
            .with_for_update()
        )
    )
    for occurrence in occurrences:
        occurrence.status = ReminderOccurrenceStatus.CANCELED
        occurrence.updated_at = now
        db.add(
            ReminderAction(
                reminder_occurrence_id=occurrence.reminder_occurrence_id,
                performed_by_user_id=actor.user_id,
                action_type=ReminderActionType.CANCEL,
                action_note=reason,
                previous_status=ReminderOccurrenceStatus.UPCOMING,
                new_status=ReminderOccurrenceStatus.CANCELED,
                action_metadata={"source": "template_reconciliation"},
                performed_at=now,
            )
        )
    db.flush()
    return len(occurrences)


def _restore_template_canceled_occurrences(
    db: Session,
    *,
    template: ReminderTemplate,
    actor: User,
    now,
) -> int:
    """Reactivate matching rows canceled by a prior template reconciliation.

    The database uniqueness rule prevents inserting a second row for the same
    template/time after a caregiver disables then re-enables a template.  We
    only restore rows with our reconciliation marker, never a caregiver's
    explicit per-occurrence cancellation.
    """
    scheduled_values = occurrence_times_for_window(
        template,
        window_start=now,
        window_end=now + timedelta(days=MATERIALIZATION_WINDOW_DAYS),
    )
    if not scheduled_values:
        return 0
    reconciliation_action = exists(
        select(ReminderAction.reminder_action_id).where(
            ReminderAction.reminder_occurrence_id
            == ReminderOccurrence.reminder_occurrence_id,
            ReminderAction.action_type == ReminderActionType.CANCEL,
            ReminderAction.action_metadata["source"].astext
            == "template_reconciliation",
        )
    )
    occurrences = list(
        db.scalars(
            select(ReminderOccurrence)
            .where(
                ReminderOccurrence.reminder_template_id
                == template.reminder_template_id,
                ReminderOccurrence.status == ReminderOccurrenceStatus.CANCELED,
                ReminderOccurrence.scheduled_at.in_(scheduled_values),
                reconciliation_action,
            )
            .with_for_update()
        )
    )
    for occurrence in occurrences:
        occurrence.status = ReminderOccurrenceStatus.UPCOMING
        occurrence.due_at = occurrence.scheduled_at + timedelta(
            minutes=template.due_after_minutes
        )
        occurrence.updated_at = now
        db.add(
            ReminderAction(
                reminder_occurrence_id=occurrence.reminder_occurrence_id,
                performed_by_user_id=actor.user_id,
                action_type=ReminderActionType.RESCHEDULE,
                action_note="Reactivated because the reminder template is active.",
                previous_status=ReminderOccurrenceStatus.CANCELED,
                new_status=ReminderOccurrenceStatus.UPCOMING,
                new_due_at=occurrence.due_at,
                action_metadata={"source": "template_reconciliation"},
                performed_at=now,
            )
        )
    db.flush()
    return len(occurrences)


def list_reminder_templates(
    db: Session,
    *,
    actor: User,
    patient_id: UUID,
    statuses: list[ReminderTemplateStatus] | None,
    limit: int,
    offset: int,
) -> tuple[list[ReminderTemplate], int]:
    _require_assigned_patient(db, actor=actor, patient_id=patient_id)
    filters = [ReminderTemplate.patient_id == patient_id]
    if statuses:
        filters.append(ReminderTemplate.status.in_(statuses))
    else:
        filters.append(ReminderTemplate.status != ReminderTemplateStatus.ARCHIVED)
    total = db.scalar(
        select(func.count(ReminderTemplate.reminder_template_id)).where(*filters)
    ) or 0
    items = list(
        db.scalars(
            select(ReminderTemplate)
            .where(*filters)
            .order_by(
                ReminderTemplate.start_date.asc(),
                ReminderTemplate.start_time.asc(),
                ReminderTemplate.reminder_template_id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return items, total


def get_reminder_template(
    db: Session, *, actor: User, template_id: UUID, lock: bool = False
) -> ReminderTemplate:
    _require_caregiver(actor)
    statement = select(ReminderTemplate).where(
        ReminderTemplate.reminder_template_id == template_id,
        ReminderTemplate.patient_id.in_(_assigned_patient_ids(actor)),
    )
    if lock:
        statement = statement.with_for_update(of=ReminderTemplate)
    template = db.scalar(statement)
    if template is None:
        raise ReminderNotFoundError("Reminder template not found.")
    return template


def _validate_effective_snooze(template: ReminderTemplate) -> None:
    if template.snooze_allowed and template.default_snooze_minutes < 1:
        raise ReminderQueryValidationError(
            "default_snooze_minutes must be at least 1 when snooze is allowed."
        )


def update_reminder_template(
    db: Session,
    *,
    actor: User,
    template_id: UUID,
    payload: ReminderTemplateUpdate,
) -> ReminderTemplate:
    template = get_reminder_template(
        db, actor=actor, template_id=template_id, lock=True
    )
    if template.status is ReminderTemplateStatus.ARCHIVED:
        raise ReminderActionConflictError("Archived reminder templates cannot be edited.")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ReminderQueryValidationError("At least one field must be provided.")
    required_fields = {
        "title",
        "category",
        "priority",
        "start_date",
        "start_time",
        "timezone",
        "due_after_minutes",
        "snooze_allowed",
        "default_snooze_minutes",
        "missed_after_minutes",
        "notification_channels",
        "status",
    }
    if any(changes.get(field) is None for field in required_fields & changes.keys()):
        raise ReminderQueryValidationError(
            "Required reminder template fields cannot be null."
        )
    schedule_changed = bool(_SCHEDULE_FIELDS & changes.keys())
    status_changed = "status" in changes and changes["status"] != template.status
    for field, value in changes.items():
        setattr(template, field, value)
    _validate_effective_snooze(template)
    now = utc_now()
    template.updated_at = now
    db.flush()
    if schedule_changed or status_changed:
        _cancel_future_occurrences(
            db,
            template=template,
            actor=actor,
            now=now,
            reason=(
                "Canceled because the reminder template schedule changed."
                if schedule_changed
                else "Canceled because the reminder template was disabled."
            ),
        )
    if template.status is ReminderTemplateStatus.ACTIVE and (
        schedule_changed or status_changed
    ):
        _restore_template_canceled_occurrences(
            db, template=template, actor=actor, now=now
        )
        materialize_reminder_occurrences(db, template=template, window_start=now)
    return template


def archive_reminder_template(
    db: Session, *, actor: User, template_id: UUID
) -> ReminderTemplate:
    template = get_reminder_template(
        db, actor=actor, template_id=template_id, lock=True
    )
    if template.status is ReminderTemplateStatus.ARCHIVED:
        return template
    archived_at = utc_now()
    template.status = ReminderTemplateStatus.ARCHIVED
    template.archived_at = archived_at
    template.updated_at = archived_at
    db.flush()
    _cancel_future_occurrences(
        db,
        template=template,
        actor=actor,
        now=archived_at,
        reason="Canceled because the reminder template was archived.",
    )
    return template
