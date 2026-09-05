from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.auth.dependencies import bearer_scheme, get_current_caregiver
from app.auth.security import decode_access_token
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.household_access.errors import AccessForbiddenError
from app.patients.schema import PatientCreate, PatientCreated
from app.patients.service import create_patient
from app.users.model import User

router = APIRouter(prefix="/api/v1/patients", tags=["Patients"])


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
