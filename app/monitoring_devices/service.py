from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.monitoring_devices.model import MonitoringDevice
from app.monitoring_devices.schema import DeviceStatusUpsert
from app.patients.model import ElderlyPatient
from app.users.model import User, UserRole


class DeviceStatusAccessError(Exception):
    """Raised when an actor cannot update the requested patient."""


class DeviceStatusConflictError(Exception):
    """Raised when the device upsert conflicts with database state."""


@dataclass(frozen=True)
class DeviceStatusUpsertResult:
    device: MonitoringDevice
    applied: bool


def _patient_for_actor(
    db: Session,
    actor: User,
    patient_id,
) -> ElderlyPatient:
    if actor.role is not UserRole.ELDERLY_PATIENT:
        raise DeviceStatusAccessError(
            "Patient device access required."
        )

    patient = db.scalar(
        select(ElderlyPatient).where(
            ElderlyPatient.patient_id == patient_id,
            ElderlyPatient.user_id == actor.user_id,
            ElderlyPatient.archived_at.is_(None),
        )
    )

    if patient is None:
        raise DeviceStatusAccessError(
            "Patient not available for this account."
        )

    return patient


def _find_device_for_update(
    db: Session,
    payload: DeviceStatusUpsert,
) -> MonitoringDevice | None:
    return db.scalar(
        select(MonitoringDevice)
        .where(
            MonitoringDevice.patient_id == payload.patient_id,
            MonitoringDevice.device_type == payload.device_type,
        )
        .with_for_update()
    )


def _apply_update(
    device: MonitoringDevice,
    payload: DeviceStatusUpsert,
) -> bool:
    # Never allow delayed/offline queued payloads to overwrite newer state.
    if (
        device.reported_at is not None
        and payload.reported_at <= device.reported_at
    ):
        return False

    previous_status = device.connection_status

    if "device_name" in payload.model_fields_set:
        device.device_name = payload.device_name

    if "device_model" in payload.model_fields_set:
        device.device_model = payload.device_model

    if "battery_percent" in payload.model_fields_set:
        device.battery_percent = payload.battery_percent

    device.connection_status = payload.connection_status
    device.reported_at = payload.reported_at
    device.last_seen_at = utc_now()

    if previous_status != payload.connection_status:
        device.status_changed_at = payload.reported_at

    return True


def upsert_device_status(
    db: Session,
    actor: User,
    payload: DeviceStatusUpsert,
) -> DeviceStatusUpsertResult:
    _patient_for_actor(
        db,
        actor,
        payload.patient_id,
    )

    try:
        device = _find_device_for_update(
            db,
            payload,
        )

        if device is None:
            now = utc_now()

            device = MonitoringDevice(
                patient_id=payload.patient_id,
                device_type=payload.device_type,
                device_name=payload.device_name,
                device_model=payload.device_model,
                battery_percent=payload.battery_percent,
                connection_status=payload.connection_status,
                reported_at=payload.reported_at,
                last_seen_at=now,
                status_changed_at=payload.reported_at,
                created_at=now,
                updated_at=now,
            )

            db.add(device)
            db.flush()
            db.commit()

            return DeviceStatusUpsertResult(
                device=device,
                applied=True,
            )

        applied = _apply_update(
            device,
            payload,
        )

        # Commit even for stale payloads so any SELECT FOR UPDATE lock
        # is released immediately.
        db.commit()

        return DeviceStatusUpsertResult(
            device=device,
            applied=applied,
        )

    except IntegrityError as exc:
        db.rollback()

        # This should primarily protect against simultaneous first-time
        # registration of the same patient/device pair.
        device = _find_device_for_update(
            db,
            payload,
        )

        if device is None:
            db.rollback()

            raise DeviceStatusConflictError(
                "Device status conflicts with current database state."
            ) from exc

        applied = _apply_update(
            device,
            payload,
        )

        db.commit()

        return DeviceStatusUpsertResult(
            device=device,
            applied=applied,
        )

    except Exception:
        db.rollback()
        raise