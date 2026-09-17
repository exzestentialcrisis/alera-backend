from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_actor
from app.db.database import get_db
from app.nudges.schema import PatientNudgeCreate, PatientNudgeRead
from app.nudges.service import (
    NudgeConflictError,
    NudgeForbiddenError,
    NudgeNotFoundError,
    create_patient_nudge,
)
from app.users.model import User


router = APIRouter(prefix="/api/v1/patients", tags=["Patient Nudges"])


@router.post(
    "/{patient_id}/nudges",
    response_model=PatientNudgeRead,
    status_code=status.HTTP_201_CREATED,
)
def send_nudge(
    patient_id: UUID,
    payload: PatientNudgeCreate,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    try:
        nudge, idempotent = create_patient_nudge(
            db,
            actor=actor,
            patient_id=patient_id,
            nudge_type=payload.nudge_type,
            client_action_id=payload.client_action_id,
        )
        db.commit()
        db.refresh(nudge)
    except NudgeForbiddenError as exc:
        db.rollback()
        raise HTTPException(403, str(exc)) from exc
    except NudgeNotFoundError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except NudgeConflictError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return {
        "nudge_id": nudge.nudge_id,
        "patient_id": nudge.patient_id,
        "sent_by_user_id": nudge.sent_by_user_id,
        "nudge_type": nudge.nudge_type,
        "client_action_id": nudge.client_action_id,
        "created_at": nudge.created_at,
        "idempotent": idempotent,
    }
