from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.activity.schema import (
    ActivityTrendRange,
    ActivityTrendResponse,
    SleepTrendRange,
    SleepTrendResponse,
)
from app.activity.service import get_activity_trend, get_sleep_trend
from app.auth.dependencies import bearer_scheme, get_current_caregiver
from app.auth.security import decode_access_token
from app.core.config import Settings, get_settings
from app.db.database import get_db
from app.health_events.model import MetricType
from app.health_events.schema import VitalTrendRange, VitalTrendResponse
from app.health_events.service import get_vital_trend
from app.household_access.errors import AccessForbiddenError
from app.monitoring_devices.schema import MonitoringDeviceRead
from app.monitoring_devices.service import list_patient_monitoring_devices
from app.patients.errors import PatientNotFoundError
from app.patients.photo_storage import (
    ALLOWED_PROFILE_PHOTO_TYPES,
    MAX_PROFILE_PHOTO_BYTES,
    PatientPhotoStorageError,
    build_profile_photo_path,
    public_profile_photo_url,
    upload_profile_photo,
)
from app.patients.schema import (
    MonitoringSettingsResponse,
    MonitoringSettingsUpdate,
    PatientCreate,
    PatientCreated,
    PatientDetail,
    PatientListResponse,
    PatientProfilePhotoResponse,
    PatientUpdate,
)
from app.patients.service import (
    MonitoringSettingsValidationError,
    create_patient,
    get_patient,
    list_patients,
    patient_read_payload,
    update_monitoring_settings,
    update_patient,
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
    "/{patient_id}/vital-trends",
    response_model=VitalTrendResponse,
    summary="Get patient vital-sign trends",
    responses={
        404: {
            "description": "Patient not found in the actor's scope.",
        },
        422: {
            "description": "Unsupported metric for vital trends.",
        },
    },
)
def read_vital_trends(
    patient_id: UUID,
    metric_type: MetricType,
    trend_range: Annotated[
        VitalTrendRange,
        Query(alias="range"),
    ] = VitalTrendRange.DAY,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        patient_row = get_patient(
            db,
            actor,
            patient_id,
        )

        return get_vital_trend(
            db,
            patient_row.patient,
            metric_type=metric_type,
            trend_range=trend_range,
        )

    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/{patient_id}/activity-trends",
    response_model=ActivityTrendResponse,
    summary="Get patient activity trends",
    responses={
        404: {
            "description": "Patient not found in the actor's scope.",
        },
    },
)
def read_activity_trends(
    patient_id: UUID,
    trend_range: Annotated[
        ActivityTrendRange,
        Query(alias="range"),
    ] = ActivityTrendRange.WEEK,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        patient_row = get_patient(
            db,
            actor,
            patient_id,
        )

        return get_activity_trend(
            db,
            patient_row.patient,
            trend_range=trend_range,
        )

    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/{patient_id}/sleep-trends",
    response_model=SleepTrendResponse,
    summary="Get patient sleep trends",
    responses={
        404: {
            "description": "Patient not found in the actor's scope.",
        },
    },
)
def read_sleep_trends(
    patient_id: UUID,
    trend_range: Annotated[
        SleepTrendRange,
        Query(alias="range"),
    ] = SleepTrendRange.WEEK,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        patient_row = get_patient(
            db,
            actor,
            patient_id,
        )

        return get_sleep_trend(
            db,
            patient_row.patient,
            trend_range=trend_range,
        )

    except PatientNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{patient_id}/profile-photo",
    response_model=PatientProfilePhotoResponse,
    summary="Upload a patient profile photo",
    responses={
        404: {"description": "Patient not found in the actor's scope."},
        413: {"description": "Profile photo exceeds the maximum size."},
        415: {"description": "Unsupported image type."},
        503: {"description": "Profile photo storage is unavailable."},
    },
)
async def upload_patient_profile_photo(
    patient_id: UUID,
    file: UploadFile = File(...),
    actor: User = Depends(get_current_caregiver),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    try:
        row = get_patient(db, actor, patient_id)

        content_type = (file.content_type or "").lower()

        if content_type not in ALLOWED_PROFILE_PHOTO_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Profile photo must be JPEG, PNG, or WebP.",
            )

        content = await file.read(MAX_PROFILE_PHOTO_BYTES + 1)

        if len(content) > MAX_PROFILE_PHOTO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Profile photo must be 5 MB or smaller.",
            )

        if not content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Profile photo cannot be empty.",
            )

        object_path = build_profile_photo_path(
            patient_id=str(patient_id),
            content_type=content_type,
        )

        upload_profile_photo(
            settings=settings,
            object_path=object_path,
            content=content,
            content_type=content_type,
        )

        row.patient.profile_photo_path = object_path
        db.commit()

        profile_photo_url = public_profile_photo_url(
            settings=settings,
            object_path=object_path,
        )

        if profile_photo_url is None:
            raise PatientPhotoStorageError("Profile photo URL could not be generated.")

        return PatientProfilePhotoResponse(
            patient_id=patient_id,
            profile_photo_url=profile_photo_url,
        )

    except PatientNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except PatientPhotoStorageError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise

    finally:
        await file.close()


@router.patch(
    "/{patient_id}",
    response_model=PatientDetail,
    summary="Update a patient and caregiver relationship",
    responses={
        403: {
            "description": (
                "The actor cannot set a relationship without an active "
                "caregiver assignment."
            )
        },
        404: {"description": "Patient not found in the actor's scope."},
    },
)
def update_patient_profile(
    patient_id: UUID,
    payload: PatientUpdate,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        result = update_patient(db, actor, patient_id, payload)
        db.commit()
        return result
    except PatientNotFoundError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except AccessForbiddenError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise


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


@router.get(
    "/{patient_id}/monitoring-devices",
    response_model=list[MonitoringDeviceRead],
    summary="List monitoring devices for a patient",
    responses={
        404: {
            "description": "Patient not found in the actor's scope.",
        }
    },
)
def read_patient_monitoring_devices(
    patient_id: UUID,
    actor: User = Depends(get_current_caregiver),
    db: Session = Depends(get_db),
):
    try:
        get_patient(db, actor, patient_id)

        return list_patient_monitoring_devices(
            db,
            patient_id,
        )

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
