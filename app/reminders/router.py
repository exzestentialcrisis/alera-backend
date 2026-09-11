from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_actor
from app.db.database import get_db
from app.reminders.enums import ReminderCategory, ReminderOccurrenceStatus
from app.reminders.errors import (
    ReminderAccessForbiddenError,
    ReminderNotFoundError,
    ReminderQueryValidationError,
)
from app.reminders.schema import (
    ReminderOccurrenceListResponse,
    ReminderOccurrenceRead,
)
from app.reminders.service import (
    get_reminder_occurrence,
    list_reminder_occurrences,
    reminder_occurrence_payload,
)
from app.users.model import User

router = APIRouter(prefix="/api/v1/reminders", tags=["Reminders"])


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, ReminderNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ReminderAccessForbiddenError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, ReminderQueryValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    raise exc


@router.get("", response_model=ReminderOccurrenceListResponse)
async def get_reminders(
    patient_id: UUID | None = None,
    from_at: datetime | None = None,
    before_at: datetime | None = None,
    statuses: Annotated[list[ReminderOccurrenceStatus] | None, Query(alias="status")] = None,
    categories: Annotated[list[ReminderCategory] | None, Query(alias="category")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    try:
        items, total = list_reminder_occurrences(
            db,
            actor=actor,
            patient_id=patient_id,
            from_at=from_at,
            before_at=before_at,
            statuses=statuses,
            categories=categories,
            limit=limit,
            offset=offset,
        )
    except (
        ReminderAccessForbiddenError,
        ReminderNotFoundError,
        ReminderQueryValidationError,
    ) as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable")
    return {
        "items": [reminder_occurrence_payload(occurrence, template) for occurrence, template in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{occurrence_id}", response_model=ReminderOccurrenceRead)
async def get_reminder(
    occurrence_id: UUID,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    try:
        occurrence, template = get_reminder_occurrence(
            db, actor=actor, occurrence_id=occurrence_id
        )
    except ReminderNotFoundError as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable")
    return reminder_occurrence_payload(occurrence, template)
