from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.errors import AuthenticationError
from app.auth.schema import (
    CaregiverLoginRequest,
    CaregiverLoginResponse,
    HouseholdValidationRequest,
    HouseholdValidationResponse,
    PatientAccessRequest,
)
from app.auth.service import (
    authenticate_caregiver,
    authenticate_patient,
    validate_household_code,
)
from app.core.config import Settings, get_settings
from app.db.database import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post(
    "/household/validate",
    response_model=HouseholdValidationResponse,
    summary="Validate a household code",
    description=(
        "Public onboarding check for an active, available household. This endpoint "
        "does not authenticate a caregiver or issue a token."
    ),
    responses={
        404: {
            "description": "The household is unknown, archived, or unavailable."
        }
    },
)
def validate_household(
    payload: HouseholdValidationRequest,
    db: Session = Depends(get_db),
):
    result = validate_household_code(db, payload)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Household not found.",
        )
    return result


@router.post("/caregiver/login", response_model=CaregiverLoginResponse)
def caregiver_login(
    payload: CaregiverLoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        return authenticate_caregiver(db, payload, settings)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.post("/patient/access", response_model=CaregiverLoginResponse)
def patient_access(
    payload: PatientAccessRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        result = authenticate_patient(db, payload, settings)
        db.commit()
        return result
    except AuthenticationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=401, detail="Invalid access code.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except Exception:
        db.rollback()
        raise
