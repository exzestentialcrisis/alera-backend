"""Transaction-scoped missed-reminder intents; delivery starts after commit."""

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.reminders.notification_service import (
    deliver_due_reminder_notifications,
    deliver_missed_reminder_notifications,
)


logger = logging.getLogger(__name__)
KEY = "alera_missed_reminder_notifications"
DUE_KEY = "alera_due_reminder_notifications"


def queue_due_reminder_notification(db: Session, occurrence_id) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    db.info.setdefault(DUE_KEY, {}).setdefault(transaction, set()).add(occurrence_id)


def queue_missed_reminder_notification(db: Session, occurrence_id) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    db.info.setdefault(KEY, {}).setdefault(transaction, set()).add(occurrence_id)


@event.listens_for(Session, "after_commit")
def _after_commit(db: Session) -> None:
    nested = db.get_nested_transaction()
    if nested is not None:
        for key in (KEY, DUE_KEY):
            pending = db.info.get(key, {})
            ids = pending.pop(nested, set())
            if ids:
                pending.setdefault(nested.parent, set()).update(ids)
        return
    pending = db.info.pop(KEY, {})
    occurrence_ids = set().union(*pending.values()) if pending else set()
    if occurrence_ids:
        try:
            deliver_missed_reminder_notifications(
                db.get_bind(), occurrence_ids
            )
        except Exception:
            logger.warning("Post-commit missed reminder notification failed.")
    due_pending = db.info.pop(DUE_KEY, {})
    due_occurrence_ids = set().union(*due_pending.values()) if due_pending else set()
    if due_occurrence_ids:
        try:
            deliver_due_reminder_notifications(db.get_bind(), due_occurrence_ids)
        except Exception:
            logger.warning("Post-commit due reminder notification failed.")


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(db: Session, transaction) -> None:
    for key in (KEY, DUE_KEY):
        pending = db.info.get(key)
        if pending is not None:
            pending.pop(transaction, None)
            if transaction.parent is None:
                db.info.pop(key, None)
