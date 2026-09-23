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
    watch_not_worn_grace: timedelta,
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

        #LOGGED_OUT is an intentional, sticky state.
        #Liveness checks must never overwrite it with DISCONNECTED.
        if (
            phone.connection_status
            is DeviceConnectionStatus.LOGGED_OUT
        ):
            # A logged-out phone must not have a phone-disconnect alert.
            set_device_alert_condition(
                db,
                patient_id=phone.patient_id,
                condition_key=ConditionKey.PHONE_DISCONNECTED,
                active=False,
            )

            # While the patient is logged out, the backend cannot
            # meaningfully determine the watch's connection state.
            watch = watches_by_patient.get(phone.patient_id)

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

                set_device_alert_condition(
                    db,
                    patient_id=watch.patient_id,
                    condition_key=ConditionKey.WATCH_DISCONNECTED,
                    active=False,
                )

                set_device_alert_condition(
                    db,
                    patient_id=watch.patient_id,
                    condition_key=ConditionKey.WATCH_NOT_WORN,
                    active=False,
                )

            continue

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

 # 3. Watch wear-status lifecycle.
    for watch in watches_by_patient.values():
        phone = phones_by_patient.get(
            watch.patient_id
        )

        # Wear status is only trustworthy while both
        # the phone and watch are currently reachable.
        watch_is_observable = (
            watch.connection_status
            is DeviceConnectionStatus.CONNECTED
            and watch.last_seen_at > cutoff
            and phone is not None
            and phone.connection_status
            is DeviceConnectionStatus.CONNECTED
            and phone.last_seen_at > cutoff
        )

        if not watch_is_observable:
            set_device_alert_condition(
                db,
                patient_id=watch.patient_id,
                condition_key=ConditionKey.WATCH_NOT_WORN,
                active=False,
            )
            continue

        # Worn / unknown / no active not-worn period.
        if (
            watch.is_worn is not False
            or watch.not_worn_since is None
        ):
            set_device_alert_condition(
                db,
                patient_id=watch.patient_id,
                condition_key=ConditionKey.WATCH_NOT_WORN,
                active=False,
            )
            continue

        grace_elapsed = (
            now - watch.not_worn_since
            >= watch_not_worn_grace
        )

        set_device_alert_condition(
            db,
            patient_id=watch.patient_id,
            condition_key=ConditionKey.WATCH_NOT_WORN,
            active=grace_elapsed,
        )

    db.commit()