from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.monitoring_devices.model import (
    DeviceConnectionStatus,
    MonitoringDevice,
    MonitoringDeviceType,
)
from app.event_evaluations.model import ConditionKey
from app.monitoring_devices.alerts import set_device_alert_condition


def check_device_liveness(
    db: Session,
    *,
    stale_after: timedelta,
) -> None:
    now = utc_now()
    cutoff = now - stale_after

    devices = db.scalars(
        select(MonitoringDevice).with_for_update()
    ).all()

    phones_by_patient = {
        device.patient_id: device
        for device in devices
        if device.device_type is MonitoringDeviceType.PHONE
    }

    watches_by_patient = {
        device.patient_id: device
        for device in devices
        if device.device_type is MonitoringDeviceType.WATCH
    }

    # 1. Phones are authoritative for their own backend reachability
    for phone in phones_by_patient.values():
        if phone.last_seen_at > cutoff:
            continue

        if (
            phone.connection_status
            is not DeviceConnectionStatus.DISCONNECTED
        ):
            phone.connection_status = (
                DeviceConnectionStatus.DISCONNECTED
            )
            phone.status_changed_at = now
            phone.updated_at = now

        set_device_alert_condition(
            db,
            patient_id=phone.patient_id,
            condition_key=ConditionKey.PHONE_DISCONNECTED,
            active=True,
        )

        # If the phone is offline, the backend cannot know
        # whether the watch itself is actually disconnected
        watch = watches_by_patient.get(
            phone.patient_id
        )

        if watch is not None:
            if (
                watch.connection_status
                is not DeviceConnectionStatus.UNKNOWN
            ):
                watch.connection_status = (
                    DeviceConnectionStatus.UNKNOWN
                )
                watch.status_changed_at = now
                watch.updated_at = now

            # UNKNOWN is not the same as disconnected
            # Any existing watch-disconnect alert must be resolved
            set_device_alert_condition(
                db,
                patient_id=watch.patient_id,
                condition_key=ConditionKey.WATCH_DISCONNECTED,
                active=False,
            )

    # 2. A stale watch is only considered disconnected
    # when itsss patient phone is still healthy
    for watch in watches_by_patient.values():
        if watch.last_seen_at > cutoff:
            continue

        phone = phones_by_patient.get(
            watch.patient_id
        )

        phone_is_healthy = (
            phone is not None
            and phone.connection_status
            is DeviceConnectionStatus.CONNECTED
            and phone.last_seen_at > cutoff
        )

        if not phone_is_healthy:
            if (
                watch.connection_status
                is not DeviceConnectionStatus.UNKNOWN
            ):
                watch.connection_status = (
                    DeviceConnectionStatus.UNKNOWN
                )
                watch.status_changed_at = now
                watch.updated_at = now

            set_device_alert_condition(
                db,
                patient_id=watch.patient_id,
                condition_key=ConditionKey.WATCH_DISCONNECTED,
                active=False,
            )

            continue

        if (
            watch.connection_status
            is not DeviceConnectionStatus.DISCONNECTED
        ):
            watch.connection_status = (
                DeviceConnectionStatus.DISCONNECTED
            )
            watch.status_changed_at = now
            watch.updated_at = now

        set_device_alert_condition(
            db,
            patient_id=watch.patient_id,
            condition_key=ConditionKey.WATCH_DISCONNECTED,
            active=True,
        )

    db.commit()