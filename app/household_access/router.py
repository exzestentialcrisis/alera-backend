from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.alerts.dependencies import get_development_actor
from app.auth.dependencies import get_current_actor
from app.db.database import get_db
from app.household_access.errors import (
    AccessConflictError,
    AccessForbiddenError,
    AccessNotFoundError,
)
from app.household_access.schema import (
    CaregiverAssignmentCreate,
    CaregiverAssignmentResponse,
    PatientAccessCodeCreate,
    PatientAccessCodeIssueResponse,
    PatientAccessCodeResponse,
)
from app.household_access.service import (
    create_assignment,
    issue_access_code,
    revoke_access_code,
    unassign,
)
from app.users.model import User

router = APIRouter(tags=["Household Access"])


def _handle(db: Session, operation):
    try:
        result = operation()
        db.commit()
        return result
    except AccessForbiddenError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except AccessNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except AccessConflictError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.post(
    "/api/v1/households/{household_id}/caregiver-assignments",
    response_model=CaregiverAssignmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def assign_caregiver(
    household_id: UUID,
    payload: CaregiverAssignmentCreate,
    actor: User = Depends(get_development_actor),
    db: Session = Depends(get_db),
):
    return _handle(
        db,
        lambda: create_assignment(
            db, household_id, payload.caregiver_user_id, payload.patient_id, actor
        ),
    )


@router.post(
    "/api/v1/households/{household_id}/caregiver-assignments/{assignment_id}/unassign",
    response_model=CaregiverAssignmentResponse,
)
def unassign_caregiver(
    household_id: UUID,
    assignment_id: UUID,
    actor: User = Depends(get_development_actor),
    db: Session = Depends(get_db),
):
    return _handle(db, lambda: unassign(db, household_id, assignment_id, actor))


@router.post(
    "/api/v1/patients/{patient_id}/access-codes",
    response_model=PatientAccessCodeIssueResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_patient_access_code(
    patient_id: UUID,
    payload: PatientAccessCodeCreate,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    code, readable = _handle(
        db, lambda: issue_access_code(db, patient_id, actor, payload.expires_in_hours)
    )
    return {
        "access_code_id": code.access_code_id,
        "patient_id": code.patient_id,
        "access_code": readable,
        "created_by_user_id": code.created_by_user_id,
        "created_at": code.created_at,
        "expires_at": code.expires_at,
        "status": code.status,
    }


@router.post(
    "/api/v1/patients/{patient_id}/access-codes/{access_code_id}/revoke",
    response_model=PatientAccessCodeResponse,
)
def revoke_patient_access_code(
    patient_id: UUID,
    access_code_id: UUID,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
):
    code = _handle(
        db, lambda: revoke_access_code(db, patient_id, access_code_id, actor)
    )
    return {
        "access_code_id": code.access_code_id,
        "patient_id": code.patient_id,
        "created_by_user_id": code.created_by_user_id,
        "created_at": code.created_at,
        "expires_at": code.expires_at,
        "used_at": code.used_at,
        "revoked_at": code.revoked_at,
        "status": code.status,
    }
