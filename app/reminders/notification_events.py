"""Transaction-scoped missed-reminder intents; delivery starts after commit."""

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.reminders.notification_service import deliver_missed_reminder_notifications


logger = logging.getLogger(__name__)
KEY = "alera_missed_reminder_notifications"


def queue_missed_reminder_notification(db: Session, occurrence_id) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    db.info.setdefault(KEY, {}).setdefault(transaction, set()).add(occurrence_id)


@event.listens_for(Session, "after_commit")
def _after_commit(db: Session) -> None:
    pending = db.info.get(KEY, {})
    nested = db.get_nested_transaction()
    if nested is not None:
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


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(db: Session, transaction) -> None:
    pending = db.info.get(KEY)
    if pending is not None:
        pending.pop(transaction, None)
        if transaction.parent is None:
            db.info.pop(KEY, None)
