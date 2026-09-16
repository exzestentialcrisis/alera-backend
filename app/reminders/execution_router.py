import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.time import utc_now
from app.db.database import get_db
from app.reminders.execution import (
    DEFAULT_MAX_BATCHES,
    MAX_BATCHES_PER_RUN,
    execute_reminder_lifecycle,
)
from app.reminders.lifecycle import (
    DEFAULT_LIFECYCLE_BATCH_SIZE,
    MAX_LIFECYCLE_BATCH_SIZE,
)


router = APIRouter(prefix="/api/v1/internal/reminders", tags=["Internal Reminders"])


class ReminderLifecycleExecutionResponse(BaseModel):
    processed: int
    marked_due: int
    marked_missed: int
    batches: int
    limit_reached: bool


def require_maintenance_secret(
    provided: Annotated[
        str | None,
        Header(alias="X-Alera-Maintenance-Secret"),
    ] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    configured = settings.reminder_lifecycle_secret
    if configured is None or not configured.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reminder lifecycle execution is not configured.",
        )
    expected = configured.get_secret_value().encode("utf-8")
    actual = (provided or "").encode("utf-8")
    if not hmac.compare_digest(actual, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid maintenance credentials.",
        )


@router.post(
    "/process-lifecycle",
    response_model=ReminderLifecycleExecutionResponse,
)
async def process_lifecycle(
    _authorized: None = Depends(require_maintenance_secret),
    batch_size: Annotated[
        int,
        Query(ge=1, le=MAX_LIFECYCLE_BATCH_SIZE),
    ] = DEFAULT_LIFECYCLE_BATCH_SIZE,
    max_batches: Annotated[
        int,
        Query(ge=1, le=MAX_BATCHES_PER_RUN),
    ] = DEFAULT_MAX_BATCHES,
    db: Session = Depends(get_db),
):
    result = execute_reminder_lifecycle(
        db,
        at=utc_now(),
        batch_size=batch_size,
        max_batches=max_batches,
    )
    return ReminderLifecycleExecutionResponse(
        processed=result.processed,
        marked_due=result.marked_due,
        marked_missed=result.marked_missed,
        batches=result.batches,
        limit_reached=result.limit_reached,
    )
