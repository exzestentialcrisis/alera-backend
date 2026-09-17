from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.activity.errors import (
    ActivityAccessError,
    ActivityConflictError,
)
from app.activity.model import (
    ActivityDailyData,
    ActivityData,
    ActivitySession,
    ActivityType,
)
from app.activity.schema import (
    ActivityDailyDataInput,
    ActivityDataUpsert,
    ActivitySessionInput,
)
from app.core.time import utc_now
from app.patients.model import ElderlyPatient
from app.users.model import User, UserRole


@dataclass(frozen=True)
class ActivityUpsertResult:
    activity: ActivityData
    daily: ActivityDailyData | None
    sessions: list[ActivitySession]


def _patient_for_actor(
    db: Session,
    actor: User,
    patient_id: UUID,
) -> ElderlyPatient:
    """
    Activity ingestion is patient-side only.

    The authenticated elderly-patient account may only submit data
    for its own ElderlyPatient record.
    """

    if actor.role is not UserRole.ELDERLY_PATIENT:
        raise ActivityAccessError(
            "Patient activity access required."
        )

    patient = db.scalar(
        select(ElderlyPatient).where(
            ElderlyPatient.patient_id == patient_id,
            ElderlyPatient.user_id == actor.user_id,
            ElderlyPatient.archived_at.is_(None),
        )
    )

    if patient is None:
        raise ActivityAccessError(
            "Patient not available for this account."
        )

    return patient


def _find_activity_for_update(
    db: Session,
    payload: ActivityDataUpsert,
) -> ActivityData | None:
    return db.scalar(
        select(ActivityData)
        .where(
            ActivityData.patient_id == payload.patient_id,
            ActivityData.activity_date == payload.activity_date,
            ActivityData.activity_type == payload.activity_type,
        )
        .with_for_update()
    )


def _get_or_create_activity(
    db: Session,
    payload: ActivityDataUpsert,
) -> ActivityData:
    activity = _find_activity_for_update(
        db,
        payload,
    )

    if activity is not None:
        return activity

    now = utc_now()

    activity = ActivityData(
        patient_id=payload.patient_id,
        activity_date=payload.activity_date,
        activity_type=payload.activity_type,
        created_at=now,
        updated_at=now,
    )

    db.add(activity)
    db.flush()

    return activity


def _find_daily_for_update(
    db: Session,
    activity_data_id: UUID,
) -> ActivityDailyData | None:
    return db.scalar(
        select(ActivityDailyData)
        .where(
            ActivityDailyData.activity_data_id
            == activity_data_id,
        )
        .with_for_update()
    )


def _apply_daily_data(
    db: Session,
    activity: ActivityData,
    payload: ActivityDailyDataInput,
) -> ActivityDailyData:
    daily = _find_daily_for_update(
        db,
        activity.activity_data_id,
    )

    now = utc_now()

    if daily is None:
        daily = ActivityDailyData(
            activity_data_id=activity.activity_data_id,
            total_duration_seconds=0,
            session_count=0,
            created_at=now,
            updated_at=now,
        )

        db.add(daily)
        db.flush()

    fields = (
        "total_steps",
        "total_duration_seconds",
        "total_distance_meters",
        "first_movement_at",
        "last_movement_at",
    )

    for field_name in fields:
        if field_name not in payload.model_fields_set:
            continue

        value = getattr(payload, field_name)

        # These numeric aggregate fields are not nullable in the
        # database once supplied.
        if (
            field_name == "total_duration_seconds"
            and value is None
        ):
            continue

        setattr(
            daily,
            field_name,
            value,
        )

    daily.updated_at = now

    return daily


def _find_session_for_update(
    db: Session,
    activity_data_id: UUID,
    external_session_id: str | None,
) -> ActivitySession | None:
    if external_session_id is None:
        return None

    return db.scalar(
        select(ActivitySession)
        .where(
            ActivitySession.activity_data_id
            == activity_data_id,
            ActivitySession.external_session_id
            == external_session_id,
        )
        .with_for_update()
    )


def _duration_seconds(
    started_at: datetime,
    ended_at: datetime | None,
) -> int | None:
    if ended_at is None:
        return None

    return int(
        (
            ended_at - started_at
        ).total_seconds()
    )


def _upsert_session(
    db: Session,
    activity: ActivityData,
    payload: ActivitySessionInput,
) -> ActivitySession:
    session = _find_session_for_update(
        db,
        activity.activity_data_id,
        payload.external_session_id,
    )

    now = utc_now()

    if session is None:
        session = ActivitySession(
            activity_data_id=activity.activity_data_id,
            external_session_id=payload.external_session_id,
            sleep_type=payload.sleep_type,
            started_at=payload.started_at,
            ended_at=payload.ended_at,
            duration_seconds=_duration_seconds(
                payload.started_at,
                payload.ended_at,
            ),
            steps=payload.steps,
            distance_meters=payload.distance_meters,
            source=payload.source,
            created_at=now,
            updated_at=now,
        )

        db.add(session)
        db.flush()

        return session

    session.sleep_type = payload.sleep_type
    session.started_at = payload.started_at
    session.ended_at = payload.ended_at

    session.duration_seconds = _duration_seconds(
        payload.started_at,
        payload.ended_at,
    )

    session.steps = payload.steps
    session.distance_meters = payload.distance_meters
    session.source = payload.source
    session.updated_at = now

    return session


def _recalculate_session_summary(
    db: Session,
    activity: ActivityData,
) -> ActivityDailyData | None:
    """
    Recalculate values that the backend owns.

    session_count always comes from stored sessions.

    For SLEEP, total duration also comes from completed sleep
    sessions rather than trusting the phone to calculate it.
    """

    daily = _find_daily_for_update(
        db,
        activity.activity_data_id,
    )

    session_count = db.scalar(
        select(
            func.count(
                ActivitySession.activity_session_id
            )
        ).where(
            ActivitySession.activity_data_id
            == activity.activity_data_id
        )
    ) or 0

    if daily is None and session_count == 0:
        return None

    now = utc_now()

    if daily is None:
        daily = ActivityDailyData(
            activity_data_id=activity.activity_data_id,
            total_duration_seconds=0,
            session_count=0,
            created_at=now,
            updated_at=now,
        )

        db.add(daily)
        db.flush()

    daily.session_count = session_count

    if activity.activity_type is ActivityType.SLEEP:
        total_duration = db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        ActivitySession.duration_seconds
                    ),
                    0,
                )
            ).where(
                ActivitySession.activity_data_id
                == activity.activity_data_id
            )
        )

        daily.total_duration_seconds = int(
            total_duration or 0
        )

    daily.updated_at = now

    return daily


def _list_sessions(
    db: Session,
    activity_data_id: UUID,
) -> list[ActivitySession]:
    return list(
        db.scalars(
            select(ActivitySession)
            .where(
                ActivitySession.activity_data_id
                == activity_data_id
            )
            .order_by(
                ActivitySession.started_at,
                ActivitySession.activity_session_id,
            )
        ).all()
    )


def upsert_activity_data(
    db: Session,
    actor: User,
    payload: ActivityDataUpsert,
) -> ActivityUpsertResult:
    """
    Create/update one patient's activity type for one calendar day.

    Parent:
        patient + date + activity type

    Daily child:
        daily aggregate values

    Session children:
        individual activity/sleep sessions
    """

    _patient_for_actor(
        db,
        actor,
        payload.patient_id,
    )

    try:
        activity = _get_or_create_activity(
            db,
            payload,
        )

        if payload.daily is not None:
            _apply_daily_data(
                db,
                activity,
                payload.daily,
            )

        for session_payload in payload.sessions:
            _upsert_session(
                db,
                activity,
                session_payload,
            )

        daily = _recalculate_session_summary(
            db,
            activity,
        )

        activity.updated_at = utc_now()

        db.commit()

        sessions = _list_sessions(
            db,
            activity.activity_data_id,
        )

        return ActivityUpsertResult(
            activity=activity,
            daily=daily,
            sessions=sessions,
        )

    except IntegrityError as exc:
        db.rollback()

        raise ActivityConflictError(
            "Activity data conflicts with current database state."
        ) from exc

    except Exception:
        db.rollback()
        raise