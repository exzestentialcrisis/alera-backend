from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_caregiver, get_current_patient
from app.db.database import get_db
from app.help_requests.model import HelpRequestStatus
from app.help_requests.schema import (
    HelpRequestCreate,
    HelpRequestListResponse,
    HelpRequestRead,
)
from app.help_requests.service import (
    HelpRequestConflictError,
    HelpRequestNotFoundError,
    HelpRequestTransitionConflictError,
    acknowledge_help_request,
    active_help_request,
    create_help_request,
    get_help_request,
    help_request_payload,
    list_help_requests,
    resolve_help_request,
)
from app.patients.model import ElderlyPatient
from app.users.model import User


router = APIRouter(
    prefix="/api/v1/help-requests",
    tags=["Help Requests"],
)


@router.post(
    "",
    response_model=HelpRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def request_help(
    payload: HelpRequestCreate,
    patient: ElderlyPatient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    try:
        request, idempotent = create_help_request(
            db,
            patient=patient,
            client_action_id=payload.client_action_id,
            message=payload.message,
        )
        db.commit()
        db.refresh(request)
    except HelpRequestConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise

    return help_request_payload(
        request,
        patient=patient,
        idempotent=idempotent,
    )


@router.get(
    "/active",
    response_model=HelpRequestRead | None,
)
def read_active_help_request(
    patient: ElderlyPatient = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    request = active_help_request(
        db,
        patient_id=patient.patient_id,
    )
    if request is None:
        return None

    return help_request_payload(request, patient=patient)


@router.get(
    "",
    response_model=HelpRequestListResponse,
)
def read_help_requests(
    statuses: Annotated[
        list[HelpRequestStatus] | None,
        Query(alias="status"),
    ] = None,
    patient_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    rows, total = list_help_requests(
        db,
        actor=actor,
        statuses=statuses,
        patient_id=patient_id,
        limit=limit,
        offset=offset,
    )

    return {
        "items": [
            help_request_payload(
                request,
                patient=patient,
                user=user,
            )
            for request, patient, user in rows
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{help_request_id}",
    response_model=HelpRequestRead,
)
def read_help_request(
    help_request_id: UUID,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        request, patient, user = get_help_request(
            db,
            help_request_id=help_request_id,
            actor=actor,
        )
    except HelpRequestNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return help_request_payload(
        request,
        patient=patient,
        user=user,
    )


def _complete_action(
    db: Session,
    *,
    action,
    help_request_id: UUID,
    actor: User,
):
    try:
        request, idempotent = action(
            db,
            help_request_id=help_request_id,
            actor=actor,
        )
        db.commit()
        db.refresh(request)
        request, patient, user = get_help_request(
            db,
            help_request_id=help_request_id,
            actor=actor,
        )
    except HelpRequestNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except HelpRequestTransitionConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise

    return help_request_payload(
        request,
        patient=patient,
        user=user,
        idempotent=idempotent,
    )


@router.post(
    "/{help_request_id}/acknowledge",
    response_model=HelpRequestRead,
)
def acknowledge(
    help_request_id: UUID,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    return _complete_action(
        db,
        action=acknowledge_help_request,
        help_request_id=help_request_id,
        actor=actor,
    )


@router.post(
    "/{help_request_id}/resolve",
    response_model=HelpRequestRead,
)
def resolve(
    help_request_id: UUID,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    return _complete_action(
        db,
        action=resolve_help_request,
        help_request_id=help_request_id,
        actor=actor,
    )
