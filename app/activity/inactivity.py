from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity.alerts import set_inactivity_alert_condition
from app.activity.model import (
    ActivityDailyData,
    ActivityData,
    ActivityType,
)
from app.monitoring_devices.model import (
    DeviceConnectionStatus,
    MonitoringDevice,
    MonitoringDeviceType,
)


@dataclass(frozen=True)
class InactivityCheckResult:
    evaluated: int = 0
    inactive: int = 0
    movement_detected: int = 0
    skipped_no_data: int = 0
    skipped_stale_steps: int = 0
    skipped_device_unavailable: int = 0


def _get_device(
    db: Session,
    *,
    patient_id,
    device_type: MonitoringDeviceType,
) -> MonitoringDevice | None:
    return db.scalar(
        select(MonitoringDevice).where(
            MonitoringDevice.patient_id == patient_id,
            MonitoringDevice.device_type == device_type,
        )
    )


def _devices_are_observable(
    db: Session,
    *,
    patient_id,
    cutoff: datetime,
) -> bool:
    phone = _get_device(
        db,
        patient_id=patient_id,
        device_type=MonitoringDeviceType.PHONE,
    )

    watch = _get_device(
        db,
        patient_id=patient_id,
        device_type=MonitoringDeviceType.WATCH,
    )

    if phone is None or watch is None:
        return False

    if (
        phone.connection_status
        is not DeviceConnectionStatus.CONNECTED
    ):
        return False

    if (
        watch.connection_status
        is not DeviceConnectionStatus.CONNECTED
    ):
        return False

    if phone.last_seen_at <= cutoff:
        return False

    if watch.last_seen_at <= cutoff:
        return False

    return True


def evaluate_inactivity_window(
    db: Session,
    *,
    window_start: datetime,
    window_end: datetime,
    device_stale_after: timedelta,
    step_stale_after: timedelta,
) -> InactivityCheckResult:
    """
    Evaluate one completed daytime inactivity window.

    Example:
        06:00 -> 14:00
        14:00 -> 22:00

    Sleep does not participate in this rule.

    No activity record, stale step data, or unavailable devices
    means UNKNOWN -- never inactivity.
    """

    if window_end <= window_start:
        raise ValueError(
            "window_end must be after window_start."
        )

    device_cutoff = (
        window_end - device_stale_after
    )

    step_cutoff = (
        window_end - step_stale_after
    )

    activity_date = window_end.date()

    activity_rows = db.scalars(
        select(ActivityData).where(
            ActivityData.activity_date == activity_date,
            ActivityData.activity_type == ActivityType.STEPS,
        )
    ).all()

    evaluated = 0
    inactive = 0
    movement_detected = 0
    skipped_no_data = 0
    skipped_stale_steps = 0
    skipped_device_unavailable = 0

    for activity in activity_rows:
        daily = db.scalar(
            select(ActivityDailyData).where(
                ActivityDailyData.activity_data_id
                == activity.activity_data_id
            )
        )

        if daily is None:
            skipped_no_data += 1
            continue

        # We need a reasonably recent steps sync at the
        # checkpoint. Otherwise missing data could be mistaken
        # for no movement.
        if daily.updated_at < step_cutoff:
            skipped_stale_steps += 1
            continue

        if not _devices_are_observable(
            db,
            patient_id=activity.patient_id,
            cutoff=device_cutoff,
        ):
            skipped_device_unavailable += 1
            continue

        evaluated += 1

        last_movement = daily.last_movement_at

        # If the phone has fresh step data explicitly showing
        # zero steps, then no movement has occurred.
        if last_movement is None:
            if daily.total_steps == 0:
                set_inactivity_alert_condition(
                    db,
                    patient_id=activity.patient_id,
                    active=True,
                )

                inactive += 1
                continue

            # We have steps but no timestamp telling us when
            # they occurred. Do not guess.
            skipped_no_data += 1
            continue

        # A timestamp after the evaluation window indicates
        # inconsistent/future data. Do not infer inactivity.
        if last_movement > window_end:
            skipped_no_data += 1
            continue

        if last_movement >= window_start:
            #Movement occurred somewhere within this 8-hour
            #monitoring period.
            set_inactivity_alert_condition(
                db,
                patient_id=activity.patient_id,
                active=False,
            )

            movement_detected += 1
            continue

        #Last movement happened before the current 8-hour
        #window and the step record is fresh.
    
        #Therefore there was no movement during this entire
        #monitored period.
        set_inactivity_alert_condition(
            db,
            patient_id=activity.patient_id,
            active=True,
        )

        inactive += 1

    db.commit()

    return InactivityCheckResult(
        evaluated=evaluated,
        inactive=inactive,
        movement_detected=movement_detected,
        skipped_no_data=skipped_no_data,
        skipped_stale_steps=skipped_stale_steps,
        skipped_device_unavailable=(
            skipped_device_unavailable
        ),
    )