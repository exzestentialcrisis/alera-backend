from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_actor
from app.db.database import get_db
from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderOccurrenceStatus,
)
from app.reminders.errors import (
    ReminderAccessForbiddenError,
    ReminderNotFoundError,
    ReminderQueryValidationError,
    ReminderActionConflictError,
)
from app.reminders.schema import (
    ReminderOccurrenceListResponse,
    ReminderOccurrenceRead,
    ReminderActionResponse,
    ReminderCompleteRequest,
    ReminderSnoozeRequest,
    ReminderCareNoteRequest,
    ReminderCaregiverCompleteRequest,
    ReminderCancelRequest,
    ReminderMissedHandledRequest,
    ReminderActionHistoryResponse,
)
from app.reminders.service import (
    get_reminder_occurrence,
    list_reminder_occurrences,
    reminder_occurrence_payload,
    reminder_action_payload,
    complete_reminder,
    snooze_reminder,
    list_reminder_actions,
    record_caregiver_reminder_action,
    complete_reminder_on_behalf,
    cancel_reminder,
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
    if isinstance(exc, ReminderActionConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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


@router.get("/{occurrence_id}/actions", response_model=ReminderActionHistoryResponse)
async def get_reminder_actions(
    occurrence_id: UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    try:
        items, total = list_reminder_actions(
            db, actor=actor, occurrence_id=occurrence_id, limit=limit, offset=offset
        )
    except ReminderNotFoundError as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable")
    return {"items": [reminder_action_payload(action) for action in items], "total": total, "limit": limit, "offset": offset}


def _run_patient_action(db: Session, operation) -> dict:
    try:
        occurrence, template, action, idempotent = operation()
        db.commit()
        return {
            "reminder": reminder_occurrence_payload(occurrence, template),
            "action": reminder_action_payload(action),
            "idempotent": idempotent,
        }
    except (
        ReminderAccessForbiddenError,
        ReminderNotFoundError,
        ReminderActionConflictError,
    ) as exc:
        db.rollback()
        _raise_http_error(exc)
        raise AssertionError("unreachable")
    except Exception:
        db.rollback()
        raise


@router.post("/{occurrence_id}/complete", response_model=ReminderActionResponse)
async def complete(
    occurrence_id: UUID,
    payload: ReminderCompleteRequest,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _run_patient_action(
        db,
        lambda: complete_reminder(
            db, actor=actor, occurrence_id=occurrence_id,
            client_action_id=payload.client_action_id, note=payload.note,
        ),
    )


@router.post("/{occurrence_id}/snooze", response_model=ReminderActionResponse)
async def snooze(
    occurrence_id: UUID,
    payload: ReminderSnoozeRequest,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _run_patient_action(
        db,
        lambda: snooze_reminder(
            db, actor=actor, occurrence_id=occurrence_id,
            client_action_id=payload.client_action_id,
            snooze_minutes=payload.snooze_minutes,
            note=payload.note,
        ),
    )


@router.post(
    "/{occurrence_id}/complete-on-behalf", response_model=ReminderActionResponse
)
async def complete_on_behalf(
    occurrence_id: UUID,
    payload: ReminderCaregiverCompleteRequest,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _run_caregiver_action(
        db,
        lambda: complete_reminder_on_behalf(
            db,
            actor=actor,
            occurrence_id=occurrence_id,
            client_action_id=payload.client_action_id,
            note=payload.note,
        ),
    )


@router.post("/{occurrence_id}/cancel", response_model=ReminderActionResponse)
async def cancel(
    occurrence_id: UUID,
    payload: ReminderCancelRequest,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _run_caregiver_action(
        db,
        lambda: cancel_reminder(
            db,
            actor=actor,
            occurrence_id=occurrence_id,
            client_action_id=payload.client_action_id,
            note=payload.note,
        ),
    )


def _run_caregiver_action(db: Session, operation) -> dict:
    return _run_patient_action(db, operation)


@router.post("/{occurrence_id}/notes", response_model=ReminderActionResponse)
async def add_note(
    occurrence_id: UUID,
    payload: ReminderCareNoteRequest,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _run_caregiver_action(
        db,
        lambda: record_caregiver_reminder_action(
            db, actor=actor, occurrence_id=occurrence_id,
            client_action_id=payload.client_action_id,
            action_type=ReminderActionType.ADD_NOTE, note=payload.note,
        ),
    )


@router.post("/{occurrence_id}/follow-ups", response_model=ReminderActionResponse)
async def follow_up(
    occurrence_id: UUID,
    payload: ReminderCareNoteRequest,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _run_caregiver_action(
        db,
        lambda: record_caregiver_reminder_action(
            db, actor=actor, occurrence_id=occurrence_id,
            client_action_id=payload.client_action_id,
            action_type=ReminderActionType.FOLLOW_UP, note=payload.note,
        ),
    )


@router.post("/{occurrence_id}/missed/handle", response_model=ReminderActionResponse)
async def mark_missed_handled(
    occurrence_id: UUID,
    payload: ReminderMissedHandledRequest,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _run_caregiver_action(
        db,
        lambda: record_caregiver_reminder_action(
            db, actor=actor, occurrence_id=occurrence_id,
            client_action_id=payload.client_action_id,
            action_type=ReminderActionType.MARK_MISSED_HANDLED, note=payload.note,
        ),
    )
