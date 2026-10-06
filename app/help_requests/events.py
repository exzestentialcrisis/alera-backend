"""Transaction-scoped help-request notification intents."""

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
KEY = "alera_help_request_notifications"


def queue_help_request_notification(
    db: Session,
    help_request_id,
    event_name: str,
) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    intent = (help_request_id, event_name)
    db.info.setdefault(KEY, {}).setdefault(transaction, set()).add(intent)


@event.listens_for(Session, "after_commit")
def _after_commit(db: Session) -> None:
    pending = db.info.get(KEY, {})
    nested = db.get_nested_transaction()
    if nested is not None:
        intents = pending.pop(nested, set())
        if intents:
            pending.setdefault(nested.parent, set()).update(intents)
        return

    pending = db.info.pop(KEY, {})
    intents = set().union(*pending.values()) if pending else set()
    if intents:
        try:
            from app.help_requests.notification_service import (
                deliver_help_request_notifications,
            )

            deliver_help_request_notifications(db.get_bind(), intents)
        except Exception:
            logger.warning("Post-commit help-request notification failed.")


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(db: Session, transaction) -> None:
    pending = db.info.get(KEY)
    if pending is not None:
        pending.pop(transaction, None)
        if transaction.parent is None:
            db.info.pop(KEY, None)
