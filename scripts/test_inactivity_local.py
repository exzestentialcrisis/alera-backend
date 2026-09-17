import os
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.activity.inactivity import evaluate_inactivity_window
from app.activity.model import (
    ActivityData,
    ActivityDailyData,
    ActivityType,
)
from app.alerts.model import Alert, AlertStatus
from app.db.database import get_session_factory
from app.event_evaluations.model import ConditionKey
from app.monitoring_devices.model import (
    DeviceConnectionStatus,
    MonitoringDevice,
    MonitoringDeviceType,
)
from app.patients.model import ElderlyPatient


DEFAULT_PATIENT_ID = UUID(
    "44444444-4444-4444-4444-444444444444"
)

PATIENT_ID = UUID(
    os.getenv(
        "ALERA_TEST_PATIENT_ID",
        str(DEFAULT_PATIENT_ID),
    )
)

TIMEZONE = ZoneInfo("Asia/Manila")

# Fake completed 8-hour monitoring window:
#
# 6:00 AM -> 2:00 PM
WINDOW_END = datetime(
    2026,
    9,
    17,
    14,
    0,
    tzinfo=TIMEZONE,
)

WINDOW_START = WINDOW_END - timedelta(hours=8)


def get_or_create_steps(
    db,
) -> tuple[ActivityData, ActivityDailyData]:
    activity = db.scalar(
        select(ActivityData).where(
            ActivityData.patient_id == PATIENT_ID,
            ActivityData.activity_date == WINDOW_END.date(),
            ActivityData.activity_type == ActivityType.STEPS,
        )
    )

    if activity is None:
        activity = ActivityData(
            patient_id=PATIENT_ID,
            activity_date=WINDOW_END.date(),
            activity_type=ActivityType.STEPS,
        )

        db.add(activity)
        db.flush()

    daily = db.scalar(
        select(ActivityDailyData).where(
            ActivityDailyData.activity_data_id
            == activity.activity_data_id,
        )
    )

    if daily is None:
        daily = ActivityDailyData(
            activity_data_id=activity.activity_data_id,
            total_steps=0,
            total_duration_seconds=0,
            session_count=0,
        )

        db.add(daily)
        db.flush()

    return activity, daily


def get_or_create_device(
    db,
    device_type: MonitoringDeviceType,
) -> MonitoringDevice:
    device = db.scalar(
        select(MonitoringDevice).where(
            MonitoringDevice.patient_id == PATIENT_ID,
            MonitoringDevice.device_type == device_type,
        )
    )

    if device is None:
        device = MonitoringDevice(
            patient_id=PATIENT_ID,
            device_type=device_type,
        )

        db.add(device)
        db.flush()

    return device


def active_inactivity_alert(db) -> Alert | None:
    return db.scalar(
        select(Alert).where(
            Alert.patient_id == PATIENT_ID,
            Alert.condition_key == ConditionKey.INACTIVITY,
            Alert.status.in_(
                (
                    AlertStatus.ACTIVE,
                    AlertStatus.ACKNOWLEDGED,
                )
            ),
        )
    )


def prepare_devices(
    db,
    *,
    watch_connected: bool = True,
) -> None:
    phone = get_or_create_device(
        db,
        MonitoringDeviceType.PHONE,
    )

    watch = get_or_create_device(
        db,
        MonitoringDeviceType.WATCH,
    )

    phone.connection_status = (
        DeviceConnectionStatus.CONNECTED
    )

    watch.connection_status = (
        DeviceConnectionStatus.CONNECTED
        if watch_connected
        else DeviceConnectionStatus.DISCONNECTED
    )

    # Fresh relative to our fake 2 PM checkpoint.
    phone.last_seen_at = (
        WINDOW_END - timedelta(minutes=1)
    )

    watch.last_seen_at = (
        WINDOW_END - timedelta(minutes=1)
    )


def run_evaluator(db):
    return evaluate_inactivity_window(
        db,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        device_stale_after=timedelta(
            seconds=150
        ),
        step_stale_after=timedelta(
            minutes=15
        ),
    )


def print_result(
    db,
    label: str,
    result,
) -> None:
    alert = active_inactivity_alert(db)

    print()
    print("=" * 60)
    print(label)
    print("=" * 60)

    print(
        "evaluated:",
        result.evaluated,
    )

    print(
        "inactive:",
        result.inactive,
    )

    print(
        "movement:",
        result.movement_detected,
    )

    print(
        "stale steps:",
        result.skipped_stale_steps,
    )

    print(
        "device unavailable:",
        result.skipped_device_unavailable,
    )

    print(
        "active inactivity alert:",
        (
            alert.alert_id
            if alert is not None
            else None
        ),
    )


def main() -> None:
    with get_session_factory()() as db:
        patient = db.get(
            ElderlyPatient,
            PATIENT_ID,
        )

        if patient is None:
            raise RuntimeError(
                f"Patient {PATIENT_ID} does not exist."
            )

        _, daily = get_or_create_steps(db)

        # -------------------------------------------------
        # CASE 1
        # Patient moved during the 6 AM -> 2 PM window.
        # Expected: NO inactivity alert.
        # -------------------------------------------------

        prepare_devices(
            db,
            watch_connected=True,
        )

        daily.total_steps = 1500

        daily.last_movement_at = (
            WINDOW_END - timedelta(hours=2)
        )

        daily.updated_at = (
            WINDOW_END - timedelta(minutes=5)
        )

        db.commit()

        result = run_evaluator(db)

        print_result(
            db,
            "CASE 1 - MOVEMENT DETECTED",
            result,
        )

        # -------------------------------------------------
        # CASE 2
        # No movement, but watch disconnected.
        #
        # Expected:
        # evaluator SKIPS inactivity.
        # NO alert.
        # -------------------------------------------------

        prepare_devices(
            db,
            watch_connected=False,
        )

        daily.total_steps = 1500

        daily.last_movement_at = (
            WINDOW_START - timedelta(minutes=1)
        )

        daily.updated_at = (
            WINDOW_END - timedelta(minutes=5)
        )

        db.commit()

        result = run_evaluator(db)

        print_result(
            db,
            "CASE 2 - WATCH DISCONNECTED",
            result,
        )

        # -------------------------------------------------
        # CASE 2B
        # Devices connected, but step data is stale.
        #
        # Expected:
        # evaluator SKIPS inactivity.
        # NO alert.
        # -------------------------------------------------

        prepare_devices(
            db,
            watch_connected=True,
        )

        daily.total_steps = 1500

        daily.last_movement_at = (
            WINDOW_START - timedelta(minutes=1)
        )

        # Older than our 15-minute freshness limit.
        daily.updated_at = (
            WINDOW_END - timedelta(minutes=30)
        )

        db.commit()

        result = run_evaluator(db)

        print_result(
            db,
            "CASE 2B - STALE STEP DATA",
            result,
        )

        # -------------------------------------------------
        # CASE 3
        # Devices connected.
        # Fresh step data.
        # No movement for the entire 8-hour window.
        #
        # Expected:
        # INACTIVITY WARNING created.
        # -------------------------------------------------

        prepare_devices(
            db,
            watch_connected=True,
        )

        daily.total_steps = 1500

        daily.last_movement_at = (
            WINDOW_START - timedelta(minutes=1)
        )

        daily.updated_at = (
            WINDOW_END - timedelta(minutes=5)
        )

        db.commit()

        result = run_evaluator(db)

        print_result(
            db,
            "CASE 3 - 8 HOURS INACTIVE",
            result,
        )

        # -------------------------------------------------
        # CASE 4
        # Movement happens again.
        #
        # Expected:
        # existing inactivity alert becomes RESOLVED.
        # -------------------------------------------------

        prepare_devices(
            db,
            watch_connected=True,
        )

        daily.total_steps = 1564

        daily.last_movement_at = (
            WINDOW_END - timedelta(minutes=30)
        )

        daily.updated_at = (
            WINDOW_END - timedelta(minutes=5)
        )

        db.commit()

        result = run_evaluator(db)

        print_result(
            db,
            "CASE 4 - MOVEMENT RESUMED",
            result,
        )

        latest_alert = db.scalar(
            select(Alert)
            .where(
                Alert.patient_id == PATIENT_ID,
                Alert.condition_key == ConditionKey.INACTIVITY,
            )
            .order_by(
                Alert.detected_at.desc()
            )
        )

        print()
        print("FINAL ALERT STATE:")

        if latest_alert is None:
            print("No inactivity alert found.")

        else:
            print(
                "alert_id:",
                latest_alert.alert_id,
            )

            print(
                "status:",
                latest_alert.status.value,
            )

            print(
                "severity:",
                latest_alert.severity.value,
            )


if __name__ == "__main__":
    main()