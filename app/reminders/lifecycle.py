from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import literal_column, or_, select
from sqlalchemy.orm import Session

from app.reminders.enums import (
    ReminderActionType,
    ReminderNotificationChannel,
    ReminderOccurrenceStatus,
)
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate
from app.reminders.notification_events import queue_missed_reminder_notification


DEFAULT_LIFECYCLE_BATCH_SIZE = 100
MAX_LIFECYCLE_BATCH_SIZE = 500


@dataclass(frozen=True)
class ReminderLifecycleResult:
    processed: int
    marked_due: int
    marked_missed: int


def _as_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include timezone information.")
    return value.astimezone(timezone.utc)


def reminder_lifecycle_target(
    *,
    status: ReminderOccurrenceStatus,
    scheduled_at: datetime,
    due_at: datetime,
    missed_after_minutes: int,
    at: datetime,
) -> ReminderOccurrenceStatus:
    """Return the state an occurrence should have at a specific instant."""
    now = _as_utc(at, field="at")
    scheduled = _as_utc(scheduled_at, field="scheduled_at")
    due = _as_utc(due_at, field="due_at")
    missed_at = due + timedelta(minutes=missed_after_minutes)

    if status is ReminderOccurrenceStatus.UPCOMING:
        if now >= missed_at:
            return ReminderOccurrenceStatus.MISSED
        if now >= scheduled:
            return ReminderOccurrenceStatus.DUE
    elif status in {
        ReminderOccurrenceStatus.DUE,
        ReminderOccurrenceStatus.SNOOZED,
    } and now >= missed_at:
        return ReminderOccurrenceStatus.MISSED
    return status


def process_reminder_lifecycle(
    db: Session,
    *,
    at: datetime,
    limit: int = DEFAULT_LIFECYCLE_BATCH_SIZE,
) -> ReminderLifecycleResult:
    """Lock and advance one batch of eligible occurrences without committing."""
    now = _as_utc(at, field="at")
    if not 1 <= limit <= MAX_LIFECYCLE_BATCH_SIZE:
        raise ValueError(
            f"limit must be between 1 and {MAX_LIFECYCLE_BATCH_SIZE}."
        )

    missed_at = (
        ReminderOccurrence.due_at
        + ReminderTemplate.missed_after_minutes
        * literal_column("INTERVAL '1 minute'")
    )
    rows = list(
        db.execute(
            select(ReminderOccurrence, ReminderTemplate)
            .join(
                ReminderTemplate,
                ReminderTemplate.reminder_template_id
                == ReminderOccurrence.reminder_template_id,
            )
            .where(
                or_(
                    (
                        ReminderOccurrence.status
                        == ReminderOccurrenceStatus.UPCOMING
                    )
                    & (ReminderOccurrence.scheduled_at <= now),
                    (
                        ReminderOccurrence.status.in_(
                            [
                                ReminderOccurrenceStatus.DUE,
                                ReminderOccurrenceStatus.SNOOZED,
                            ]
                        )
                    )
                    & (missed_at <= now),
                )
            )
            .order_by(
                ReminderOccurrence.scheduled_at.asc(),
                ReminderOccurrence.reminder_occurrence_id.asc(),
            )
            .limit(limit)
            .with_for_update(of=ReminderOccurrence, skip_locked=True)
        ).all()
    )

    marked_due = 0
    marked_missed = 0
    for occurrence, template in rows:
        previous_status = occurrence.status
        target = reminder_lifecycle_target(
            status=previous_status,
            scheduled_at=occurrence.scheduled_at,
            due_at=occurrence.due_at,
            missed_after_minutes=template.missed_after_minutes,
            at=now,
        )
        if target is previous_status:
            continue
        occurrence.status = target
        occurrence.updated_at = now
        if target is ReminderOccurrenceStatus.DUE:
            marked_due += 1
            continue

        marked_missed += 1
        db.add(
            ReminderAction(
                reminder_occurrence_id=occurrence.reminder_occurrence_id,
                performed_by_user_id=None,
                action_type=ReminderActionType.MARK_MISSED,
                action_note="Automatically marked missed after the reminder deadline.",
                previous_status=previous_status,
                new_status=ReminderOccurrenceStatus.MISSED,
                action_metadata={
                    "source": "reminder_lifecycle",
                    "automated": True,
                },
                performed_at=now,
            )
        )
        if template.notification_channels is ReminderNotificationChannel.PUSH:
            queue_missed_reminder_notification(
                db,
                occurrence.reminder_occurrence_id,
            )

    db.flush()
    return ReminderLifecycleResult(
        processed=marked_due + marked_missed,
        marked_due=marked_due,
        marked_missed=marked_missed,
    )
