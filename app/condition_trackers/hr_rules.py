from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import enum
from statistics import median
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.event_evaluations.model import ConditionKey
from app.health_events.model import HealthEvent, MetricType, ValidationStatus


HR_BUCKET_SIZE = timedelta(seconds=15)
HR_WARNING_WINDOW = timedelta(minutes=2)
HR_WARNING_REQUIRED_BUCKETS = 7
HR_INTERMITTENT_WINDOW = timedelta(minutes=5)
HR_INTERMITTENT_REQUIRED_BUCKETS = 6
HR_INTERMITTENT_MINIMUM_SPAN = timedelta(minutes=2)
HR_RECOVERY_WINDOW = timedelta(seconds=90)
HR_RECOVERY_REQUIRED_BUCKETS = 7


class HrWarningQualification(str, enum.Enum):
    SUSTAINED = "SUSTAINED"
    INTERMITTENT = "INTERMITTENT"


@dataclass(frozen=True)
class HeartRateSample:
    recorded_at: datetime
    value: Decimal


@dataclass(frozen=True)
class HeartRateBucket:
    started_at: datetime
    value: Decimal


def load_realtime_hr_samples(
    db: Session,
    patient_id: UUID,
    started_at: datetime,
    ended_at: datetime,
) -> list[HeartRateSample]:
    rows = db.execute(
        select(HealthEvent.recorded_at, HealthEvent.numeric_value)
        .where(
            HealthEvent.patient_id == patient_id,
            HealthEvent.metric_type == MetricType.HEART_RATE,
            HealthEvent.validation_status == ValidationStatus.VALID_REALTIME,
            HealthEvent.numeric_value.is_not(None),
            HealthEvent.recorded_at >= started_at,
            HealthEvent.recorded_at <= ended_at,
        )
        .order_by(HealthEvent.recorded_at, HealthEvent.created_at)
    ).all()
    return [
        HeartRateSample(recorded_at=recorded_at, value=Decimal(value))
        for recorded_at, value in rows
    ]


def bucket_heart_rate_samples(
    samples: list[HeartRateSample],
    *,
    started_at: datetime,
    ended_at: datetime,
) -> list[HeartRateBucket]:
    """Collapse raw callbacks into fixed 15-second median observations."""
    bucket_seconds = HR_BUCKET_SIZE.total_seconds()
    grouped: dict[int, list[Decimal]] = defaultdict(list)
    for sample in samples:
        if sample.recorded_at < started_at or sample.recorded_at > ended_at:
            continue
        index = int((sample.recorded_at - started_at).total_seconds() // bucket_seconds)
        grouped[index].append(sample.value)

    return [
        HeartRateBucket(
            started_at=started_at + (HR_BUCKET_SIZE * index),
            value=median(values),
        )
        for index, values in sorted(grouped.items())
    ]


def _is_abnormal(
    value: Decimal,
    condition_key: ConditionKey,
    *,
    normal_min: Decimal,
    normal_max: Decimal,
) -> bool:
    if condition_key == ConditionKey.HR_HIGH:
        return value > normal_max
    if condition_key == ConditionKey.HR_LOW:
        return value < normal_min
    raise ValueError(f"Unsupported heart-rate condition: {condition_key.value}")


def _is_normal(
    value: Decimal,
    *,
    normal_min: Decimal,
    normal_max: Decimal,
) -> bool:
    return normal_min <= value <= normal_max


def qualify_hr_warning(
    db: Session,
    *,
    patient_id: UUID,
    condition_key: ConditionKey,
    occurrence_started_at: datetime,
    observed_at: datetime,
    normal_min: Decimal,
    normal_max: Decimal,
) -> HrWarningQualification | None:
    samples = load_realtime_hr_samples(
        db,
        patient_id,
        max(occurrence_started_at, observed_at - HR_INTERMITTENT_WINDOW),
        observed_at,
    )
    return qualify_hr_warning_samples(
        samples,
        condition_key=condition_key,
        occurrence_started_at=occurrence_started_at,
        observed_at=observed_at,
        normal_min=normal_min,
        normal_max=normal_max,
    )


def qualify_hr_warning_samples(
    samples: list[HeartRateSample],
    *,
    condition_key: ConditionKey,
    occurrence_started_at: datetime,
    observed_at: datetime,
    normal_min: Decimal,
    normal_max: Decimal,
) -> HrWarningQualification | None:
    sustained_started_at = observed_at - HR_WARNING_WINDOW
    if occurrence_started_at <= sustained_started_at:
        sustained_buckets = bucket_heart_rate_samples(
            samples,
            started_at=sustained_started_at,
            ended_at=observed_at,
        )
        abnormal_count = sum(
            _is_abnormal(
                bucket.value,
                condition_key,
                normal_min=normal_min,
                normal_max=normal_max,
            )
            for bucket in sustained_buckets
        )
        if abnormal_count >= HR_WARNING_REQUIRED_BUCKETS:
            return HrWarningQualification.SUSTAINED

    intermittent_started_at = max(
        occurrence_started_at,
        observed_at - HR_INTERMITTENT_WINDOW,
    )
    intermittent_buckets = bucket_heart_rate_samples(
        samples,
        started_at=intermittent_started_at,
        ended_at=observed_at,
    )
    abnormal_buckets = [
        bucket
        for bucket in intermittent_buckets
        if _is_abnormal(
            bucket.value,
            condition_key,
            normal_min=normal_min,
            normal_max=normal_max,
        )
    ]
    if (
        len(abnormal_buckets) >= HR_INTERMITTENT_REQUIRED_BUCKETS
        and abnormal_buckets[-1].started_at - abnormal_buckets[0].started_at
        >= HR_INTERMITTENT_MINIMUM_SPAN
    ):
        return HrWarningQualification.INTERMITTENT
    return None


def hr_recovery_confirmed(
    db: Session,
    *,
    patient_id: UUID,
    observed_at: datetime,
    normal_min: Decimal,
    normal_max: Decimal,
) -> bool:
    started_at = observed_at - HR_RECOVERY_WINDOW
    samples = load_realtime_hr_samples(
        db,
        patient_id,
        started_at,
        observed_at,
    )
    return hr_recovery_confirmed_from_samples(
        samples,
        observed_at=observed_at,
        normal_min=normal_min,
        normal_max=normal_max,
    )


def hr_recovery_confirmed_from_samples(
    samples: list[HeartRateSample],
    *,
    observed_at: datetime,
    normal_min: Decimal,
    normal_max: Decimal,
) -> bool:
    started_at = observed_at - HR_RECOVERY_WINDOW
    buckets = bucket_heart_rate_samples(
        samples,
        started_at=started_at,
        ended_at=observed_at,
    )
    return (
        len(buckets) >= HR_RECOVERY_REQUIRED_BUCKETS
        and all(
            _is_normal(
                bucket.value,
                normal_min=normal_min,
                normal_max=normal_max,
            )
            for bucket in buckets
        )
    )
