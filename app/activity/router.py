from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.activity.errors import (
    ActivityAccessError,
    ActivityConflictError,
)
from app.activity.schema import (
    ActivityDataResponse,
    ActivityDataUpsert,
)
from app.activity.service import (
    ActivityUpsertResult,
    upsert_activity_data,
)
from app.auth.dependencies import get_current_actor
from app.db.database import get_db
from app.users.model import User


router = APIRouter(
    prefix="/api/v1/activity-data",
    tags=["Activity Data"],
)


def _response_payload(
    result: ActivityUpsertResult,
) -> dict:
    activity = result.activity
    daily = result.daily

    return {
        "activity_data_id": activity.activity_data_id,
        "patient_id": activity.patient_id,
        "activity_date": activity.activity_date,
        "activity_type": activity.activity_type,
        "daily": (
            {
                "activity_daily_data_id": (
                    daily.activity_daily_data_id
                ),
                "total_steps": daily.total_steps,
                "total_duration_seconds": (
                    daily.total_duration_seconds
                ),
                "total_distance_meters": (
                    daily.total_distance_meters
                ),
                "session_count": daily.session_count,
                "first_movement_at": (
                    daily.first_movement_at
                ),
                "last_movement_at": (
                    daily.last_movement_at
                ),
                "created_at": daily.created_at,
                "updated_at": daily.updated_at,
            }
            if daily is not None
            else None
        ),
        "sessions": [
            {
                "activity_session_id": (
                    session.activity_session_id
                ),
                "external_session_id": (
                    session.external_session_id
                ),
                "sleep_type": session.sleep_type,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "duration_seconds": (
                    session.duration_seconds
                ),
                "steps": session.steps,
                "distance_meters": (
                    session.distance_meters
                ),
                "source": session.source,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
            }
            for session in result.sessions
        ],
        "created_at": activity.created_at,
        "updated_at": activity.updated_at,
    }


@router.post(
    "",
    response_model=ActivityDataResponse,
    status_code=status.HTTP_200_OK,
)
def upsert_activity(
    payload: ActivityDataUpsert,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> ActivityDataResponse:
    try:
        result = upsert_activity_data(
            db,
            actor,
            payload,
        )

        return ActivityDataResponse.model_validate(
            _response_payload(result)
        )

    except ActivityAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc

    except ActivityConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc