from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.monitoring_devices.model import (
    DeviceConnectionStatus,
    MonitoringDevice,
    MonitoringDeviceType,
)
from app.monitoring_devices.schema import DeviceStatusUpsert
from app.patients.model import ElderlyPatient
from app.users.model import User, UserRole
from app.event_evaluations.model import ConditionKey
from app.monitoring_devices.alerts import set_device_alert_condition


class DeviceStatusAccessError(Exception):
    """Raised when an actor cannot update the requested patient."""


class DeviceStatusConflictError(Exception):
    """Raised when the device upsert conflicts with database state."""


@dataclass(frozen=True)
class DeviceStatusUpsertResult:
    device: MonitoringDevice
    applied: bool

def list_patient_monitoring_devices(
    db: Session,
    patient_id,
) -> list[MonitoringDevice]:
    return list(
        db.scalars(
            select(MonitoringDevice)
            .where(
                MonitoringDevice.patient_id == patient_id,
            )
            .order_by(MonitoringDevice.device_type)
        ).all()
    )

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

def _mark_watch_unknown_if_phone_unavailable(
    db: Session,
    device: MonitoringDevice,
) -> None:
    if (
        device.device_type is not MonitoringDeviceType.PHONE
        or device.connection_status
        not in {
            DeviceConnectionStatus.DISCONNECTED,
            DeviceConnectionStatus.LOGGED_OUT,
        }
    ):
        return

    watch = db.scalar(
        select(MonitoringDevice)
        .where(
            MonitoringDevice.patient_id == device.patient_id,
            MonitoringDevice.device_type
            == MonitoringDeviceType.WATCH,
        )
        .with_for_update()
    )

    if watch is None:
        return

    now = utc_now()

    if (
        watch.connection_status
        is not DeviceConnectionStatus.UNKNOWN
    ):
        watch.connection_status = DeviceConnectionStatus.UNKNOWN
        watch.status_changed_at = now
        watch.updated_at = now

    # If the phone is unavailable, we cannot reliably determine
    # whether the watch itself is disconnected or being worn.
    set_device_alert_condition(
        db,
        patient_id=device.patient_id,
        condition_key=ConditionKey.WATCH_DISCONNECTED,
        active=False,
    )

    set_device_alert_condition(
        db,
        patient_id=device.patient_id,
        condition_key=ConditionKey.WATCH_NOT_WORN,
        active=False,
    )

def _apply_watch_wear_status(
    device: MonitoringDevice,
    payload: DeviceStatusUpsert,
) -> None:
    if (
        device.device_type
        is not MonitoringDeviceType.WATCH
    ):
        return

    if "is_worn" not in payload.model_fields_set:
        return

    device.is_worn = payload.is_worn

    if payload.is_worn is False:
        if device.not_worn_since is None:
            device.not_worn_since = payload.reported_at

        return

    # true or unknown breaks the continuous
    # "not worn" period.
    device.not_worn_since = None

def _apply_update(
    db: Session,
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

    _apply_watch_wear_status(device, payload,)   

    if (
        device.device_type is MonitoringDeviceType.PHONE
        and previous_status is DeviceConnectionStatus.LOGGED_OUT
    ):
        effective_status = DeviceConnectionStatus.LOGGED_OUT
    else:
        effective_status = payload.connection_status

    device.connection_status = effective_status
    device.reported_at = payload.reported_at
    device.last_seen_at = utc_now()

    if previous_status != effective_status:
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
            _apply_watch_wear_status(
                device,
                payload,
            )

            db.add(device)
            db.flush()

            _mark_watch_unknown_if_phone_unavailable(db, device,)
            _sync_device_alerts(db, device,)

            db.commit()

            return DeviceStatusUpsertResult(
                device=device,
                applied=True,
            )

        applied = _apply_update(
            db,
            device,
            payload,
                )
        if applied:
            _mark_watch_unknown_if_phone_unavailable(
            db,
            device,
        )

        _sync_device_alerts(
        db,
        device,
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
                db,
                device,
                payload,
            )
        if applied:
                _mark_watch_unknown_if_phone_unavailable(
                db,
                device,
            )
        _sync_device_alerts(
        db,
        device,
        )
        db.commit()

        return DeviceStatusUpsertResult(
            device=device,
            applied=applied,
        )

    except Exception:
        db.rollback()
        raise

def _sync_device_alerts(
    db: Session,
    device: MonitoringDevice,
) -> None:
    if device.device_type is MonitoringDeviceType.PHONE:
        disconnected_condition = ConditionKey.PHONE_DISCONNECTED
        battery_condition = ConditionKey.PHONE_BATTERY_LOW
    else:
        disconnected_condition = ConditionKey.WATCH_DISCONNECTED
        battery_condition = ConditionKey.WATCH_BATTERY_LOW

    # Connection alert lifecycle.
    if (
        device.device_type is MonitoringDeviceType.PHONE
        and device.connection_status
        is DeviceConnectionStatus.LOGGED_OUT
    ):
        # Logout is intentional, so a phone-disconnect alert
        # must not remain active.
        set_device_alert_condition(
            db,
            patient_id=device.patient_id,
            condition_key=ConditionKey.PHONE_DISCONNECTED,
            active=False,
        )

        # Logout has its own dedicated alert.
        set_device_alert_condition(
            db,
            patient_id=device.patient_id,
            condition_key=ConditionKey.PATIENT_LOGGED_OUT,
            active=True,
        )

    elif device.connection_status is DeviceConnectionStatus.DISCONNECTED:
        set_device_alert_condition(
            db,
            patient_id=device.patient_id,
            condition_key=disconnected_condition,
            active=True,
        )

    elif device.connection_status is DeviceConnectionStatus.CONNECTED:
        set_device_alert_condition(
            db,
            patient_id=device.patient_id,
            condition_key=disconnected_condition,
            active=False,
        )

        # A successfully connected phone means the patient
        # is no longer logged out.
        if device.device_type is MonitoringDeviceType.PHONE:
            set_device_alert_condition(
                db,
                patient_id=device.patient_id,
                condition_key=ConditionKey.PATIENT_LOGGED_OUT,
                active=False,
            )

    # UNKNOWN intentionally does nothing.
    # We cannot claim recovery while reachability is unknown.

    # Battery alert lifecycle.
    if device.battery_percent is not None:
        set_device_alert_condition(
            db,
            patient_id=device.patient_id,
            condition_key=battery_condition,
            active=device.battery_percent < 20,
        )

    # Wear-status recovery is immediate.
    # Activation is handled by the liveness loop after the grace period.
    if (
        device.device_type is MonitoringDeviceType.WATCH
        and device.is_worn is not False
    ):
        set_device_alert_condition(
            db,
            patient_id=device.patient_id,
            condition_key=ConditionKey.WATCH_NOT_WORN,
            active=False,
        )

def logout_patient_phone(
    db: Session,
    actor: User,
) -> MonitoringDevice:
    if actor.role is not UserRole.ELDERLY_PATIENT:
        raise DeviceStatusAccessError(
            "Patient device access required."
        )

    patient = db.scalar(
        select(ElderlyPatient).where(
            ElderlyPatient.user_id == actor.user_id,
            ElderlyPatient.archived_at.is_(None),
        )
    )

    if patient is None:
        raise DeviceStatusAccessError(
            "Patient not available for this account."
        )

    now = utc_now()

    phone = db.scalar(
        select(MonitoringDevice)
        .where(
            MonitoringDevice.patient_id == patient.patient_id,
            MonitoringDevice.device_type
            == MonitoringDeviceType.PHONE,
        )
        .with_for_update()
    )

    if phone is None:
        phone = MonitoringDevice(
            patient_id=patient.patient_id,
            device_type=MonitoringDeviceType.PHONE,
            connection_status=DeviceConnectionStatus.LOGGED_OUT,
            reported_at=now,
            last_seen_at=now,
            status_changed_at=now,
            created_at=now,
            updated_at=now,
        )

        db.add(phone)
        db.flush()

    else:
        if (
            phone.connection_status
            is not DeviceConnectionStatus.LOGGED_OUT
        ):
            phone.connection_status = (
                DeviceConnectionStatus.LOGGED_OUT
            )
            phone.status_changed_at = now

        phone.reported_at = now
        phone.last_seen_at = now
        phone.updated_at = now

    _mark_watch_unknown_if_phone_unavailable(
        db,
        phone,
    )

    _sync_device_alerts(
        db,
        phone,
    )

    db.commit()
    db.refresh(phone)

    return phone