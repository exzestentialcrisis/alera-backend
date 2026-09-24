from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_actor
from app.db.database import get_db
from app.monitoring_devices.schema import (
    DeviceStatusResponse,
    DeviceStatusUpsert,
)

from app.monitoring_devices.service import (
    DeviceStatusAccessError,
    DeviceStatusConflictError,
    logout_patient_phone,
    upsert_device_status,
)

from app.monitoring_devices.schema import (
    DeviceStatusResponse,
    DeviceStatusUpsert,
    PatientLogoutResponse,
)

from app.users.model import User


router = APIRouter(
    prefix="/api/v1/device-status",
    tags=["Device Status"],
)


@router.post(
    "",
    response_model=DeviceStatusResponse,
    status_code=status.HTTP_200_OK,
)
def update_device_status(
    payload: DeviceStatusUpsert,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> DeviceStatusResponse:
    try:
        result = upsert_device_status(
            db,
            actor,
            payload,
        )

        device = result.device

        return DeviceStatusResponse(
            device_id=device.device_id,
            patient_id=device.patient_id,
            device_type=device.device_type,
            device_name=device.device_name,
            device_model=device.device_model,
            battery_percent=device.battery_percent,
            connection_status=device.connection_status,
            reported_at=device.reported_at,
            last_seen_at=device.last_seen_at,
            status_changed_at=device.status_changed_at,
            created_at=device.created_at,
            updated_at=device.updated_at,
            applied=result.applied,
        )

    except DeviceStatusAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    except DeviceStatusConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

@router.post(
    "/logout",
    response_model=PatientLogoutResponse,
    status_code=status.HTTP_200_OK,
)
def logout_patient_device(
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> PatientLogoutResponse:
    try:
        phone = logout_patient_phone(
            db,
            actor,
        )

        return PatientLogoutResponse(
            patient_id=phone.patient_id,
            device_id=phone.device_id,
            connection_status=phone.connection_status,
            status_changed_at=phone.status_changed_at,
        )

    except DeviceStatusAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc