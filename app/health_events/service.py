from datetime import timedelta

from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.event_evaluations.model import EventEvaluation, EvaluationSeverity
from app.event_evaluations.service import evaluate_event
from app.health_events.errors import (
    ExternalEventConflictError,
    HealthEventConstraintError,
    PatientNotFoundError,
)
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.health_events.schema import (
    HealthEventCreate,
    VitalTrendRange,
    VitalTrendResolution,
)
from app.patients.model import ElderlyPatient

VITAL_TREND_WINDOWS = {
    VitalTrendRange.DAY: timedelta(hours=24),
    VitalTrendRange.WEEK: timedelta(days=7),
    VitalTrendRange.MONTH: timedelta(days=30),
}


VITAL_TREND_BUCKETS = {
    VitalTrendRange.DAY: (
        "1 hour",
        VitalTrendResolution.HOUR,
    ),
    VitalTrendRange.WEEK: (
        "1 day",
        VitalTrendResolution.DAY,
    ),
    VitalTrendRange.MONTH: (
        "1 day",
        VitalTrendResolution.DAY,
    ),
}


VITAL_TREND_UNITS = {
    MetricType.HEART_RATE: "bpm",
    MetricType.SPO2: "%",
}


ACCEPTED_TREND_STATUSES = (
    ValidationStatus.VALID_REALTIME,
    ValidationStatus.DELAYED_USABLE,
)


def _payload_matches(
    existing: HealthEvent,
    event_data: HealthEventCreate,
) -> bool:
    return all(
        getattr(existing, field_name) == field_value
        for field_name, field_value in event_data.model_dump().items()
    )


from app.health_events.model import HealthEvent, MetricType, ValidationStatus


def _find_external_event(
    db: Session,
    external_event_id: str | None,
) -> HealthEvent | None:
    if external_event_id is None:
        return None
    return db.scalar(
        select(HealthEvent).where(
            HealthEvent.external_event_id == external_event_id,
        )
    )


def _resolve_duplicate(
    existing: HealthEvent,
    event_data: HealthEventCreate,
) -> HealthEvent:
    if not _payload_matches(existing, event_data):
        raise ExternalEventConflictError(
            "external_event_id is already used by a different event."
        )
    return existing


def create_health_event(
    db: Session,
    event_data: HealthEventCreate,
) -> HealthEvent:
    try:
        patient = db.get(ElderlyPatient, event_data.patient_id)
        if patient is None:
            raise PatientNotFoundError("Patient not found.")

        existing = _find_external_event(db, event_data.external_event_id)
        if existing is not None:
            result = _resolve_duplicate(existing, event_data)
            db.commit()
            return result

        health_event = HealthEvent(
            **event_data.model_dump(),
        )

        db.add(health_event)
        db.flush()

        if event_data.validation_status != ValidationStatus.INVALID:
            evaluate_event(
                db=db,
                event=health_event,
            )

        db.commit()

        return health_event

    except IntegrityError as exc:
        db.rollback()
        existing = _find_external_event(db, event_data.external_event_id)
        if existing is not None:
            result = _resolve_duplicate(existing, event_data)
            db.commit()
            return result
        db.rollback()
        raise HealthEventConstraintError(
            "Health event conflicts with current database state."
        ) from exc
    except Exception:
        db.rollback()
        raise


def get_vital_trend(
    db: Session,
    patient: ElderlyPatient,
    *,
    metric_type: MetricType,
    trend_range: VitalTrendRange,
) -> dict:
    if metric_type not in VITAL_TREND_UNITS:
        raise ValueError("Vital trends are only available for HEART_RATE and SPO2.")

    to_at = utc_now()
    from_at = to_at - VITAL_TREND_WINDOWS[trend_range]

    bucket_interval, resolution = VITAL_TREND_BUCKETS[trend_range]

    base_filters = (
        HealthEvent.patient_id == patient.patient_id,
        HealthEvent.metric_type == metric_type,
        HealthEvent.validation_status.in_(ACCEPTED_TREND_STATUSES),
        HealthEvent.numeric_value.is_not(None),
        HealthEvent.recorded_at >= from_at,
        HealthEvent.recorded_at <= to_at,
    )

    # -----------------------------
    # FULL-RANGE SUMMARY
    # -----------------------------

    aggregate = db.execute(
        select(
            func.avg(HealthEvent.numeric_value).label("average"),
            func.min(HealthEvent.numeric_value).label("minimum"),
            func.max(HealthEvent.numeric_value).label("maximum"),
            func.count(HealthEvent.event_id).label("reading_count"),
        ).where(*base_filters)
    ).one()

    latest_value = db.scalar(
        select(HealthEvent.numeric_value)
        .where(*base_filters)
        .order_by(
            HealthEvent.recorded_at.desc(),
            HealthEvent.received_at.desc(),
            HealthEvent.event_id.desc(),
        )
        .limit(1)
    )

    if aggregate.reading_count:
        summary = {
            "latest": (float(latest_value) if latest_value is not None else None),
            "average": round(float(aggregate.average), 2),
            "minimum": float(aggregate.minimum),
            "maximum": float(aggregate.maximum),
            "reading_count": aggregate.reading_count,
        }
    else:
        summary = {
            "latest": None,
            "average": None,
            "minimum": None,
            "maximum": None,
            "reading_count": 0,
        }

    # -----------------------------
    # PATIENT MONITORING RANGE
    # -----------------------------

    if metric_type == MetricType.HEART_RATE:
        thresholds = {
            "normal_min": float(patient.normal_hr_min),
            "normal_max": float(patient.normal_hr_max),
        }
    else:
        thresholds = {
            "normal_min": float(patient.usual_spo2_min),
            "normal_max": (
                float(patient.usual_spo2_max)
                if patient.usual_spo2_max is not None
                else None
            ),
        }

    # -----------------------------
    # GRAPH BUCKETS
    # -----------------------------

    # date_bin anchors buckets to the beginning of our requested
    # range rather than arbitrary calendar boundaries.
    #
    # 24h -> 24 rolling hourly buckets
    # 7d  -> 7 rolling daily buckets
    # 30d -> 30 rolling daily buckets
    bucket_at = func.date_bin(
        text(f"INTERVAL '{bucket_interval}'"),
        HealthEvent.recorded_at,
        from_at,
    ).label("bucket_at")

    severity_rank = case(
        (
            EventEvaluation.severity == EvaluationSeverity.CRITICAL,
            3,
        ),
        (
            EventEvaluation.severity == EvaluationSeverity.WARNING,
            2,
        ),
        (
            EventEvaluation.severity == EvaluationSeverity.INFO,
            1,
        ),
        else_=0,
    )

    bucket_rows = db.execute(
        select(
            bucket_at,
            func.avg(HealthEvent.numeric_value).label("average"),
            func.min(HealthEvent.numeric_value).label("minimum"),
            func.max(HealthEvent.numeric_value).label("maximum"),
            func.count(HealthEvent.event_id).label("reading_count"),
            func.max(severity_rank).label("severity_rank"),
        )
        .outerjoin(
            EventEvaluation,
            EventEvaluation.event_id == HealthEvent.event_id,
        )
        .where(*base_filters)
        .group_by(bucket_at)
        .order_by(bucket_at.asc())
    ).all()

    severity_by_rank = {
        3: EvaluationSeverity.CRITICAL,
        2: EvaluationSeverity.WARNING,
        1: EvaluationSeverity.INFO,
    }

    points = [
        {
            "recorded_at": row.bucket_at,
            "value": round(float(row.average), 2),
            "minimum": float(row.minimum),
            "maximum": float(row.maximum),
            "reading_count": row.reading_count,
            "severity": severity_by_rank.get(int(row.severity_rank or 0)),
        }
        for row in bucket_rows
    ]

    return {
        "patient_id": patient.patient_id,
        "metric_type": metric_type,
        "unit": VITAL_TREND_UNITS[metric_type],
        "range": trend_range,
        "resolution": resolution,
        "from_at": from_at,
        "to_at": to_at,
        "summary": summary,
        "thresholds": thresholds,
        "points": points,
    }
