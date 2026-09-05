from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_caregiver
from app.alerts.errors import AlertNotFoundError, AlertTransitionConflictError
from app.alerts.model import AlertStatus
from app.alerts.schema import (
    AlertActionHistoryResponse,
    AlertActionResponse,
    AlertDetail,
    AlertListResponse,
    AlertRead,
    FalseAlarmRequest,
    InterventionRequest,
    NoteRequest,
    OptionalNoteRequest,
)
from app.alerts.service import (
    acknowledge_alert,
    add_alert_note,
    alert_display_payload,
    get_alert_detail,
    list_alert_actions,
    list_alerts,
    log_alert_intervention,
    mark_false_alarm,
    resolve_alert,
)
from app.db.database import get_db
from app.event_evaluations.model import ConditionKey, EvaluationSeverity
from app.users.model import User

router = APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, AlertNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if isinstance(exc, AlertTransitionConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    raise exc


def _run_action(
    db: Session,
    operation: Callable[[], tuple],
) -> dict:
    try:
        alert, action, idempotent = operation()
        db.commit()
        return {
            "alert": alert,
            "action": action,
            "idempotent": idempotent,
        }
    except (AlertNotFoundError, AlertTransitionConflictError) as exc:
        db.rollback()
        _raise_http_error(exc)
        raise AssertionError("unreachable")
    except Exception:
        db.rollback()
        raise


@router.get("", response_model=AlertListResponse)
async def get_alerts(
    patient_id: UUID | None = None,
    statuses: Annotated[
        list[AlertStatus] | None,
        Query(alias="status"),
    ] = None,
    severity: EvaluationSeverity | None = None,
    condition_key: ConditionKey | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    items, total = list_alerts(
        db,
        patient_id=patient_id,
        statuses=statuses,
        severity=severity,
        condition_key=condition_key,
        limit=limit,
        offset=offset,
        actor=actor,
    )
    return {
        "items": [
            alert_display_payload(alert, evaluation, event, patient, user)
            for alert, evaluation, event, patient, user in items
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{alert_id}", response_model=AlertDetail)
async def get_alert(
    alert_id: UUID,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        alert, evaluation, event, latest_action, patient, user = get_alert_detail(
            db,
            alert_id,
            actor,
        )
    except AlertNotFoundError as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable")
    return {
        **alert_display_payload(alert, evaluation, event, patient, user),
        "triggering_event": event,
        "triggering_evaluation": evaluation,
        "latest_action": latest_action,
    }


@router.get(
    "/{alert_id}/actions",
    response_model=AlertActionHistoryResponse,
)
async def get_alert_actions(
    alert_id: UUID,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        return {"items": list_alert_actions(db, alert_id, actor)}
    except AlertNotFoundError as exc:
        _raise_http_error(exc)
        raise AssertionError("unreachable")


@router.post(
    "/{alert_id}/acknowledge",
    response_model=AlertActionResponse,
)
async def acknowledge(
    alert_id: UUID,
    payload: OptionalNoteRequest,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    return _run_action(
        db,
        lambda: acknowledge_alert(db, alert_id, actor, payload.note),
    )


@router.post("/{alert_id}/resolve", response_model=AlertActionResponse)
async def resolve(
    alert_id: UUID,
    payload: OptionalNoteRequest,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    return _run_action(
        db,
        lambda: resolve_alert(db, alert_id, actor, payload.note),
    )


@router.post(
    "/{alert_id}/false-alarm",
    response_model=AlertActionResponse,
)
async def false_alarm(
    alert_id: UUID,
    payload: FalseAlarmRequest,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    return _run_action(
        db,
        lambda: mark_false_alarm(db, alert_id, actor, payload.reason),
    )


@router.post("/{alert_id}/notes", response_model=AlertActionResponse)
async def add_note(
    alert_id: UUID,
    payload: NoteRequest,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    return _run_action(
        db,
        lambda: add_alert_note(db, alert_id, actor, payload.note),
    )


@router.post(
    "/{alert_id}/interventions",
    response_model=AlertActionResponse,
)
async def add_intervention(
    alert_id: UUID,
    payload: InterventionRequest,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    return _run_action(
        db,
        lambda: log_alert_intervention(
            db,
            alert_id,
            actor,
            payload.intervention_type.value,
            payload.note,
        ),
    )
