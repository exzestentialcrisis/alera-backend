import asyncio
import logging
from contextlib import suppress
from datetime import timedelta

from app.core.config import Settings
from app.db.database import get_session_factory
from app.monitoring_devices.liveness import check_device_liveness

logger = logging.getLogger(__name__)


async def device_liveness_loop(
    settings: Settings,
) -> None:
    while True:
        await asyncio.sleep(
            settings.device_liveness_check_seconds
        )

        try:
            def run_check() -> None:
                db = get_session_factory()()

                try:
                    check_device_liveness(
                        db,
                        stale_after=timedelta(
                            seconds=settings.device_heartbeat_stale_seconds
                        ),
                        watch_not_worn_grace=timedelta(
                            seconds=settings.watch_not_worn_grace_seconds
                        ),
                    )
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.close()

            await asyncio.to_thread(
                run_check
            )

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.warning(
                "Device liveness check failed."
            )


async def stop_device_liveness_task(
    task: asyncio.Task,
) -> None:
    task.cancel()

    with suppress(asyncio.CancelledError):
        await task