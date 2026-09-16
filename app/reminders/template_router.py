from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_actor
from app.db.database import get_db
from app.reminders.enums import ReminderTemplateStatus
from app.reminders.errors import (
    ReminderAccessForbiddenError,
    ReminderActionConflictError,
    ReminderNotFoundError,
    ReminderQueryValidationError,
)
from app.reminders.template_schema import (
    ReminderTemplateCreate,
    ReminderTemplateListResponse,
    ReminderTemplateRead,
    ReminderTemplateUpdate,
)
from app.reminders.template_service import (
    archive_reminder_template,
    create_reminder_template,
    get_reminder_template,
    list_reminder_templates,
    reminder_template_payload,
    update_reminder_template,
)
from app.users.model import User


router = APIRouter(prefix="/api/v1/reminder-templates", tags=["Reminder Templates"])


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, ReminderNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ReminderAccessForbiddenError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, ReminderQueryValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    if isinstance(exc, ReminderActionConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise exc


def _commit_template(db: Session, operation) -> dict:
    try:
        template = operation()
        db.commit()
        db.refresh(template)
        return reminder_template_payload(template)
    except (
        ReminderAccessForbiddenError,
        ReminderNotFoundError,
        ReminderQueryValidationError,
        ReminderActionConflictError,
    ) as exc:
        db.rollback()
        _raise_http_error(exc)
        raise AssertionError("unreachable")
    except Exception:
        db.rollback()
        raise


@router.post("", response_model=ReminderTemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: ReminderTemplateCreate,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _commit_template(
        db,
        lambda: create_reminder_template(db, actor=actor, payload=payload),
    )


@router.get("", response_model=ReminderTemplateListResponse)
async def read_templates(
    patient_id: UUID,
    statuses: Annotated[
        list[ReminderTemplateStatus] | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    try:
        items, total = list_reminder_templates(
            db,
            actor=actor,
            patient_id=patient_id,
            statuses=statuses,
            limit=limit,
            offset=offset,
        )
    except (
        ReminderAccessForbiddenError,
        ReminderNotFoundError,
    ) as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable")
    return {
        "items": [reminder_template_payload(item) for item in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{template_id}", response_model=ReminderTemplateRead)
async def read_template(
    template_id: UUID,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    try:
        return reminder_template_payload(
            get_reminder_template(db, actor=actor, template_id=template_id)
        )
    except (
        ReminderAccessForbiddenError,
        ReminderNotFoundError,
    ) as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable")


@router.patch("/{template_id}", response_model=ReminderTemplateRead)
async def patch_template(
    template_id: UUID,
    payload: ReminderTemplateUpdate,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _commit_template(
        db,
        lambda: update_reminder_template(
            db, actor=actor, template_id=template_id, payload=payload
        ),
    )


@router.post("/{template_id}/archive", response_model=ReminderTemplateRead)
async def archive_template(
    template_id: UUID,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    return _commit_template(
        db,
        lambda: archive_reminder_template(
            db, actor=actor, template_id=template_id
        ),
    )
