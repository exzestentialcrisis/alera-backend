"""Transaction-scoped patient nudge intents; delivery starts after commit."""

import logging

from sqlalchemy import event
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)
KEY = "alera_patient_nudges"


def queue_patient_nudge(db: Session, nudge_id) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    db.info.setdefault(KEY, {}).setdefault(transaction, set()).add(nudge_id)


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
    nudge_ids = set().union(*pending.values()) if pending else set()
    if nudge_ids:
        try:
            from app.nudges.notification_service import deliver_patient_nudges

            deliver_patient_nudges(db.get_bind(), nudge_ids)
        except Exception:
            logger.warning("Post-commit patient nudge delivery failed.")


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(db: Session, transaction) -> None:
    pending = db.info.get(KEY)
    if pending is not None:
        pending.pop(transaction, None)
        if transaction.parent is None:
            db.info.pop(KEY, None)
