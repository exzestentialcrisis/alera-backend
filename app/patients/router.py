from uuid import UUID
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.auth.dependencies import bearer_scheme, get_current_caregiver
from app.auth.security import decode_access_token
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.household_access.errors import AccessForbiddenError
from app.patients.errors import PatientNotFoundError
from app.patients.schema import (
    PatientCreate,
    PatientCreated,
    PatientDetail,
    PatientListResponse,
    MonitoringSettingsResponse,
    MonitoringSettingsUpdate,
)
from app.patients.service import (
    create_patient,
    get_patient,
    list_patients,
    patient_read_payload,
    update_monitoring_settings,
    MonitoringSettingsValidationError,
)
from app.users.model import User

router = APIRouter(prefix="/api/v1/patients", tags=["Patients"])


@router.get(
    "",
    response_model=PatientListResponse,
    summary="List patients visible to the caregiver",
    description=(
        "Returns non-archived patients in active households within the requesting "
        "caregiver's assignment or care administrator's ownership scope."
    ),
)
def read_patients(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[str | None, Query(max_length=150)] = None,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    rows, total = list_patients(
        db,
        actor,
        search=search.strip() if search and search.strip() else None,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [patient_read_payload(row, detail=False) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{patient_id}",
    response_model=PatientDetail,
    summary="Get a patient visible to the caregiver",
    responses={404: {"description": "Patient not found in the actor's scope."}},
)
def read_patient(
    patient_id: UUID,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        return patient_read_payload(get_patient(db, actor, patient_id), detail=True)
    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.patch(
    "/{patient_id}/monitoring-settings",
    response_model=MonitoringSettingsResponse,
    responses={404: {"description": "Patient not found in the actor's scope."}},
)
def update_settings(
    patient_id: UUID,
    payload: MonitoringSettingsUpdate,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        result = update_monitoring_settings(db, actor, patient_id, payload)
        db.commit()
        return result
    except PatientNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MonitoringSettingsValidationError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("", response_model=PatientCreated, status_code=201)
def create(
    payload: PatientCreate,
    actor: User = Depends(get_current_caregiver),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    # The caregiver dependency has validated this bearer token. Use its household
    # claim, never a client-selected household or an inferred first assignment.
    claims = decode_access_token(
        credentials.credentials, secret=settings.alera_jwt_secret or ""
    )
    try:
        result = create_patient(db, actor, UUID(claims["household_id"]), payload)
        db.commit()
        return result
    except AccessForbiddenError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
