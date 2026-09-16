from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.condition_trackers.hr_rules import (
    HeartRateSample,
    HrWarningQualification,
    bucket_heart_rate_samples,
    hr_recovery_confirmed_from_samples,
    qualify_hr_warning_samples,
)
from app.event_evaluations.model import ConditionKey


BASE_TIME = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
NORMAL_MIN = Decimal("60")
NORMAL_MAX = Decimal("100")


def samples(values, *, interval_seconds=15, started_at=BASE_TIME):
    return [
        HeartRateSample(
            recorded_at=started_at + timedelta(seconds=index * interval_seconds),
            value=Decimal(value),
        )
        for index, value in enumerate(values)
    ]


def qualification(values, condition=ConditionKey.HR_HIGH):
    observations = samples(values)
    return qualify_hr_warning_samples(
        observations,
        condition_key=condition,
        occurrence_started_at=BASE_TIME,
        observed_at=observations[-1].recorded_at,
        normal_min=NORMAL_MIN,
        normal_max=NORMAL_MAX,
    )


def test_bucket_uses_median_to_reject_one_second_spike():
    observations = [
        HeartRateSample(BASE_TIME + timedelta(seconds=second), Decimal(value))
        for second, value in ((0, "78"), (1, "79"), (2, "180"))
    ]

    buckets = bucket_heart_rate_samples(
        observations,
        started_at=BASE_TIME,
        ended_at=BASE_TIME + timedelta(seconds=14),
    )

    assert len(buckets) == 1
    assert buckets[0].value == Decimal("79")


def test_seven_of_nine_high_buckets_qualify_sustained_warning():
    assert qualification(
        ["110", "112", "115", "78", "116", "118", "80", "120", "114"]
    ) is HrWarningQualification.SUSTAINED


def test_five_of_nine_buckets_do_not_qualify_warning():
    assert qualification(
        ["110", "112", "78", "79", "116", "118", "80", "78", "114"]
    ) is None


def test_seven_of_nine_low_buckets_qualify_sustained_warning():
    assert qualification(
        ["55", "52", "50", "78", "54", "53", "80", "51", "49"],
        ConditionKey.HR_LOW,
    ) is HrWarningQualification.SUSTAINED


def test_spread_out_abnormality_qualifies_intermittent_warning():
    observations = samples(
        ["110", "78", "78", "110", "110", "78", "78", "110", "110", "110"],
        interval_seconds=30,
    )

    result = qualify_hr_warning_samples(
        observations,
        condition_key=ConditionKey.HR_HIGH,
        occurrence_started_at=BASE_TIME,
        observed_at=observations[-1].recorded_at,
        normal_min=NORMAL_MIN,
        normal_max=NORMAL_MAX,
    )

    assert result is HrWarningQualification.INTERMITTENT


def test_clustered_abnormality_does_not_qualify_intermittent_warning():
    observations = samples(["110"] * 6, interval_seconds=15)

    result = qualify_hr_warning_samples(
        observations,
        condition_key=ConditionKey.HR_HIGH,
        occurrence_started_at=BASE_TIME,
        observed_at=observations[-1].recorded_at,
        normal_min=NORMAL_MIN,
        normal_max=NORMAL_MAX,
    )

    assert result is None


def test_recovery_requires_ninety_elapsed_seconds_of_normal_buckets():
    six_buckets = samples(["78"] * 6)
    seven_buckets = samples(["78"] * 7)

    assert hr_recovery_confirmed_from_samples(
        six_buckets,
        observed_at=six_buckets[-1].recorded_at,
        normal_min=NORMAL_MIN,
        normal_max=NORMAL_MAX,
    ) is False
    assert hr_recovery_confirmed_from_samples(
        seven_buckets,
        observed_at=seven_buckets[-1].recorded_at,
        normal_min=NORMAL_MIN,
        normal_max=NORMAL_MAX,
    ) is True


def test_abnormal_bucket_cancels_recovery():
    observations = samples(["78", "78", "78", "110", "78", "78", "78"])

    assert hr_recovery_confirmed_from_samples(
        observations,
        observed_at=observations[-1].recorded_at,
        normal_min=NORMAL_MIN,
        normal_max=NORMAL_MAX,
    ) is False
