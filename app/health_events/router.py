from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_patient
from app.patients.model import ElderlyPatient


from app.db.database import get_db
from app.health_events.errors import (
    ExternalEventConflictError,
    HealthEventConstraintError,
    PatientNotFoundError,
)
from app.health_events.schema import (
    HealthEventCreate,
    HealthEventResponse,
)
from app.health_events.service import create_health_event

router = APIRouter(
    prefix="/api/v1/health-events",
    tags=["Health Events"],
)


@router.post(
    "",
    response_model=HealthEventResponse,
)

def create_event(
    event: HealthEventCreate,
    db: Session = Depends(get_db),
    patient: ElderlyPatient = Depends(get_current_patient),
):
    if event.patient_id != patient.patient_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Patient identity mismatch.",
        )

    try:
        return create_health_event(db, event)

    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except ExternalEventConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except HealthEventConstraintError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
