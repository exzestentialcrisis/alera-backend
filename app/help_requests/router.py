from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_patient
from app.db.database import get_db
from app.help_requests.schema import HelpRequestCreate, HelpRequestRead
from app.help_requests.service import (
    HelpRequestConflictError,
    active_help_request,
    create_help_request,
)
from app.patients.model import ElderlyPatient


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

    return {
        "help_request_id": request.help_request_id,
        "patient_id": request.patient_id,
        "status": request.status,
        "message": request.message,
        "client_action_id": request.client_action_id,
        "requested_at": request.requested_at,
        "acknowledged_by_user_id": request.acknowledged_by_user_id,
        "acknowledged_at": request.acknowledged_at,
        "resolved_by_user_id": request.resolved_by_user_id,
        "resolved_at": request.resolved_at,
        "updated_at": request.updated_at,
        "idempotent": idempotent,
    }


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

    return {
        "help_request_id": request.help_request_id,
        "patient_id": request.patient_id,
        "status": request.status,
        "message": request.message,
        "client_action_id": request.client_action_id,
        "requested_at": request.requested_at,
        "acknowledged_by_user_id": request.acknowledged_by_user_id,
        "acknowledged_at": request.acknowledged_at,
        "resolved_by_user_id": request.resolved_by_user_id,
        "resolved_at": request.resolved_at,
        "updated_at": request.updated_at,
        "idempotent": False,
    }
