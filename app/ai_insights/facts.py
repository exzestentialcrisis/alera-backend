from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.activity.schema import ActivityTrendRange, SleepTrendRange
from app.activity.service import get_activity_trend, get_sleep_trend
from app.ai_insights.schema import (
    ActivityFacts,
    AlertFacts,
    InsightFactPacket,
    SleepFacts,
    VitalFacts,
)
from app.alerts.model import Alert, AlertStatus
from app.event_evaluations.model import EvaluationSeverity
from app.health_events.model import MetricType
from app.health_events.schema import VitalTrendRange, VitalTrendResponse
from app.health_events.service import get_vital_trend
from app.patients.model import ElderlyPatient


def _vital_facts(trend: VitalTrendResponse) -> VitalFacts:
    return VitalFacts(
        unit=trend.unit,
        latest=trend.summary.latest,
        average=trend.summary.average,
        minimum=trend.summary.minimum,
        maximum=trend.summary.maximum,
        reading_count=trend.summary.reading_count,
        normal_min=trend.thresholds.normal_min,
        normal_max=trend.thresholds.normal_max,
    )


def _alert_facts(
    rows: Iterable[tuple[EvaluationSeverity, AlertStatus]],
) -> AlertFacts:
    rows = list(rows)
    return AlertFacts(
        total=len(rows),
        active=sum(
            status in {AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED}
            for _, status in rows
        ),
        warning=sum(severity is EvaluationSeverity.WARNING for severity, _ in rows),
        critical=sum(severity is EvaluationSeverity.CRITICAL for severity, _ in rows),
    )


def build_insight_fact_packet(
    db: Session,
    patient: ElderlyPatient,
) -> InsightFactPacket:
    """Build a verified seven-day packet. No generative model runs here."""
    heart_rate = VitalTrendResponse.model_validate(
        get_vital_trend(
            db,
            patient,
            metric_type=MetricType.HEART_RATE,
            trend_range=VitalTrendRange.WEEK,
        )
    )
    spo2 = VitalTrendResponse.model_validate(
        get_vital_trend(
            db,
            patient,
            metric_type=MetricType.SPO2,
            trend_range=VitalTrendRange.WEEK,
        )
    )
    activity = get_activity_trend(db, patient, ActivityTrendRange.WEEK)
    sleep = get_sleep_trend(db, patient, SleepTrendRange.WEEK)

    alert_rows = db.execute(
        select(Alert.severity, Alert.status).where(
            Alert.patient_id == patient.patient_id,
            Alert.detected_at >= heart_rate.from_at,
            Alert.detected_at <= heart_rate.to_at,
        )
    ).all()

    activity_summary = activity.summary
    sleep_summary = sleep.summary
    missing_data = []
    if heart_rate.summary.reading_count == 0:
        missing_data.append("heart_rate")
    if spo2.summary.reading_count == 0:
        missing_data.append("spo2")
    if activity_summary.days_with_data == 0:
        missing_data.append("activity")
    if sleep_summary.nights_with_data == 0:
        missing_data.append("sleep")

    return InsightFactPacket(
        patient_id=patient.patient_id,
        from_at=heart_rate.from_at,
        to_at=heart_rate.to_at,
        heart_rate=_vital_facts(heart_rate),
        spo2=_vital_facts(spo2),
        activity=ActivityFacts(
            average_steps_per_day=activity_summary.average_steps_per_day,
            highest_steps=(
                activity_summary.highest_day.total_steps
                if activity_summary.highest_day
                else None
            ),
            highest_date=(
                activity_summary.highest_day.activity_date
                if activity_summary.highest_day
                else None
            ),
            lowest_steps=(
                activity_summary.lowest_day.total_steps
                if activity_summary.lowest_day
                else None
            ),
            lowest_date=(
                activity_summary.lowest_day.activity_date
                if activity_summary.lowest_day
                else None
            ),
            days_with_data=activity_summary.days_with_data,
        ),
        sleep=SleepFacts(
            latest_duration_seconds=(
                sleep_summary.latest_night.duration_seconds
                if sleep_summary.latest_night
                else None
            ),
            latest_date=(
                sleep_summary.latest_night.activity_date
                if sleep_summary.latest_night
                else None
            ),
            average_duration_seconds=sleep_summary.average_duration_seconds,
            longest_duration_seconds=(
                sleep_summary.longest_night.duration_seconds
                if sleep_summary.longest_night
                else None
            ),
            shortest_duration_seconds=(
                sleep_summary.shortest_night.duration_seconds
                if sleep_summary.shortest_night
                else None
            ),
            nights_with_data=sleep_summary.nights_with_data,
        ),
        alerts=_alert_facts(alert_rows),
        missing_data=missing_data,
    )
