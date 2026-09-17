import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.activity.inactivity import evaluate_inactivity_window
from app.core.config import Settings
from app.core.time import utc_now
from app.db.database import get_session_factory


logger = logging.getLogger(__name__)


def _next_inactivity_window(
    settings: Settings,
) -> tuple[datetime, datetime]:
    """
    Return the next completed 8-hour daytime window.

    With the current settings:
        06:00 -> 14:00
        14:00 -> 22:00
    """

    timezone = ZoneInfo(
        settings.inactivity_timezone
    )

    now = utc_now().astimezone(timezone)

    start_hour = (
        settings.inactivity_day_start_hour
    )

    end_hour = (
        settings.inactivity_day_end_hour
    )

    threshold_hours = (
        settings.inactivity_threshold_hours
    )

    if threshold_hours <= 0:
        raise ValueError(
            "inactivity_threshold_hours must be positive."
        )

    daytime_hours = end_hour - start_hour

    if daytime_hours <= 0:
        raise ValueError(
            "inactivity_day_end_hour must be after "
            "inactivity_day_start_hour."
        )

    if daytime_hours % threshold_hours != 0:
        raise ValueError(
            "Daytime monitoring window must divide evenly "
            "into inactivity threshold windows."
        )

    today_start = now.replace(
        hour=start_hour,
        minute=0,
        second=0,
        microsecond=0,
    )

    windows: list[
        tuple[datetime, datetime]
    ] = []

    window_start = today_start

    while (
        window_start.hour < end_hour
        and window_start.date() == now.date()
    ):
        window_end = (
            window_start
            + timedelta(
                hours=threshold_hours
            )
        )

        windows.append(
            (
                window_start,
                window_end,
            )
        )

        window_start = window_end

    # Find the next checkpoint today.
    for start, end in windows:
        if end > now:
            return start, end

    # Today's checkpoints have passed.
    # Next one is tomorrow's first window.
    tomorrow_start = (
        today_start
        + timedelta(days=1)
    )

    return (
        tomorrow_start,
        tomorrow_start
        + timedelta(
            hours=threshold_hours
        ),
    )


async def inactivity_schedule_loop(
    settings: Settings,
) -> None:
    while True:
        window_start, window_end = (
            _next_inactivity_window(
                settings
            )
        )

        timezone = ZoneInfo(
            settings.inactivity_timezone
        )

        now = utc_now().astimezone(
            timezone
        )

        sleep_seconds = max(
            0.0,
            (
                window_end - now
            ).total_seconds(),
        )

        logger.info(
            "Next inactivity evaluation at %s",
            window_end.isoformat(),
        )

        try:
            await asyncio.sleep(
                sleep_seconds
            )

            def run_check() -> None:
                db = (
                    get_session_factory()()
                )

                try:
                    result = (
                        evaluate_inactivity_window(
                            db,
                            window_start=window_start,
                            window_end=window_end,
                            device_stale_after=timedelta(
                                seconds=(
                                    settings
                                    .device_heartbeat_stale_seconds
                                )
                            ),
                            step_stale_after=timedelta(
                                seconds=(
                                    settings
                                    .activity_step_stale_seconds
                                )
                            ),
                        )
                    )

                    logger.info(
                        (
                            "Inactivity evaluation complete: "
                            "evaluated=%s inactive=%s "
                            "movement=%s no_data=%s "
                            "stale_steps=%s "
                            "device_unavailable=%s"
                        ),
                        result.evaluated,
                        result.inactive,
                        result.movement_detected,
                        result.skipped_no_data,
                        result.skipped_stale_steps,
                        result.skipped_device_unavailable,
                    )

                except Exception:
                    db.rollback()
                    raise

                finally:
                    db.close()

            await asyncio.to_thread(
                run_check
            )

            # Avoid ever selecting the exact same
            # checkpoint again because of clock precision.
            await asyncio.sleep(1)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception(
                "Inactivity evaluation failed."
            )

            # Prevent a broken configuration/database
            # from creating a tight retry loop.
            await asyncio.sleep(60)


async def stop_inactivity_task(
    task: asyncio.Task,
) -> None:
    task.cancel()

    with suppress(
        asyncio.CancelledError
    ):
        await task