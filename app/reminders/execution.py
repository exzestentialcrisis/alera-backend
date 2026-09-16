from dataclasses import dataclass
from datetime import datetime
import logging

from sqlalchemy.orm import Session

from app.reminders.lifecycle import (
    DEFAULT_LIFECYCLE_BATCH_SIZE,
    MAX_LIFECYCLE_BATCH_SIZE,
    process_reminder_lifecycle,
)


logger = logging.getLogger(__name__)
DEFAULT_MAX_BATCHES = 10
MAX_BATCHES_PER_RUN = 20


@dataclass(frozen=True)
class ReminderLifecycleExecutionResult:
    processed: int
    marked_due: int
    marked_missed: int
    batches: int
    limit_reached: bool


def execute_reminder_lifecycle(
    db: Session,
    *,
    at: datetime,
    batch_size: int = DEFAULT_LIFECYCLE_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
) -> ReminderLifecycleExecutionResult:
    """Drain eligible reminder work, committing each bounded batch."""
    if not 1 <= batch_size <= MAX_LIFECYCLE_BATCH_SIZE:
        raise ValueError(
            f"batch_size must be between 1 and {MAX_LIFECYCLE_BATCH_SIZE}."
        )
    if not 1 <= max_batches <= MAX_BATCHES_PER_RUN:
        raise ValueError(
            f"max_batches must be between 1 and {MAX_BATCHES_PER_RUN}."
        )

    processed = 0
    marked_due = 0
    marked_missed = 0
    batches = 0
    last_batch_was_full = False

    for _ in range(max_batches):
        try:
            result = process_reminder_lifecycle(
                db,
                at=at,
                limit=batch_size,
            )
            if result.processed == 0:
                db.rollback()
                last_batch_was_full = False
                break
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Reminder lifecycle batch failed.")
            raise

        batches += 1
        processed += result.processed
        marked_due += result.marked_due
        marked_missed += result.marked_missed
        last_batch_was_full = result.processed == batch_size
        logger.info(
            "Reminder lifecycle batch processed.",
            extra={
                "reminder_lifecycle_batch": batches,
                "reminder_lifecycle_processed": result.processed,
                "reminder_lifecycle_marked_due": result.marked_due,
                "reminder_lifecycle_marked_missed": result.marked_missed,
            },
        )
        if not last_batch_was_full:
            break

    limit_reached = batches == max_batches and last_batch_was_full
    logger.info(
        "Reminder lifecycle execution finished.",
        extra={
            "reminder_lifecycle_batches": batches,
            "reminder_lifecycle_processed": processed,
            "reminder_lifecycle_marked_due": marked_due,
            "reminder_lifecycle_marked_missed": marked_missed,
            "reminder_lifecycle_limit_reached": limit_reached,
        },
    )
    return ReminderLifecycleExecutionResult(
        processed=processed,
        marked_due=marked_due,
        marked_missed=marked_missed,
        batches=batches,
        limit_reached=limit_reached,
    )
