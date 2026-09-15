"""Transaction-scoped notification intents; no delivery before the outer commit."""

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.notifications.service import deliver_alert_notifications

logger = logging.getLogger(__name__)
KEY = "alera_new_alert_notifications"


def queue_alert_notification(
    db: Session,
    alert: Alert,
    *,
    include_acknowledged: bool = False,
) -> None:
    if alert.status is not AlertStatus.ACTIVE and not (
        include_acknowledged and alert.status is AlertStatus.ACKNOWLEDGED
    ):
        return
    transaction = db.get_nested_transaction() or db.get_transaction()
    intent = (alert.alert_id, include_acknowledged)
    db.info.setdefault(KEY, {}).setdefault(transaction, set()).add(intent)


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
    intents = set().union(*pending.values()) if pending else set()
    if intents:
        try:
            deliver_alert_notifications(db.get_bind(), intents)
        except Exception:
            # A notification failure must never make a committed ingestion fail.
            logger.warning("Post-commit alert notification failed.")


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(db, transaction):
    pending = db.info.get(KEY)
    if pending is not None:
        pending.pop(transaction, None)
        if transaction.parent is None:
            db.info.pop(KEY, None)
