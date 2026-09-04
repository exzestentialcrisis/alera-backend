"""Create an idempotent caregiver demo using the production ingestion pipeline."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.core.time import utc_now
from app.db.database import get_session_factory
from app.event_evaluations.model import ConditionKey
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event
from app.households.model import Household
from app.patients.model import ElderlyPatient
from app.users.model import User

DEMO_HOUSEHOLD_ID = UUID("33333333-3333-3333-3333-333333333333")
DEMO_PATIENT_ID = UUID("a076ecdb-ae38-4f84-b490-e714977027ee")
DEMO_HOUSEHOLD_CODE = "4V8F-29HC"
DEMO_PATIENT_DISPLAY_NAME = "Alera Test Patient"
DEMO_EVENT_PREFIX = "alera-demo-v2"
DEMO_EVENT_OFFSETS = {
    "hr-normal": timedelta(),
    "hr-critical": timedelta(minutes=1),
    "spo2-normal": timedelta(minutes=2),
    "spo2-critical": timedelta(minutes=3),
}


@dataclass(frozen=True)
class DemoSeedResult:
    patient: ElderlyPatient
    alerts: tuple[Alert, ...]


def _get_fixture_patient(db: Session) -> ElderlyPatient:
    patient = db.get(ElderlyPatient, DEMO_PATIENT_ID)
    if patient is None:
        raise RuntimeError(
            "Required auth-development fixture patient "
            f"{DEMO_PATIENT_ID} does not exist. Load the auth-development "
            "fixture before running this seed."
        )

    household = db.get(Household, DEMO_HOUSEHOLD_ID)
    if household is None:
        raise RuntimeError(
            f"Required auth-development fixture household {DEMO_HOUSEHOLD_ID} "
            "does not exist."
        )
    if patient.household_id != household.household_id:
        raise RuntimeError("Fixture patient is not linked to the required household.")
    if household.household_code != DEMO_HOUSEHOLD_CODE:
        raise RuntimeError(
            "Fixture household has an unexpected household code: "
            f"expected {DEMO_HOUSEHOLD_CODE}, got {household.household_code}."
        )

    patient_user = db.get(User, patient.user_id)
    display_name = patient.nickname or (
        patient_user.full_name if patient_user is not None else None
    )
    if display_name != DEMO_PATIENT_DISPLAY_NAME:
        raise RuntimeError(
            "Fixture patient has an unexpected display name: "
            f"expected {DEMO_PATIENT_DISPLAY_NAME!r}, got {display_name!r}."
        )
    return patient


def _demo_start(db: Session, patient: ElderlyPatient) -> datetime:
    """Choose one reusable anchor newer than the patient's tracker watermarks."""
    external_ids = [
        f"{DEMO_EVENT_PREFIX}-{suffix}" for suffix in DEMO_EVENT_OFFSETS
    ]
    existing = db.scalars(
        select(HealthEvent).where(HealthEvent.external_event_id.in_(external_ids))
    ).all()
    if existing:
        anchors = {
            event.recorded_at
            - DEMO_EVENT_OFFSETS[event.external_event_id.removeprefix(
                f"{DEMO_EVENT_PREFIX}-"
            )]
            for event in existing
            if event.external_event_id is not None
        }
        if len(anchors) != 1:
            raise RuntimeError("Existing demo events have inconsistent timestamps.")
        return anchors.pop()

    latest_recorded_at = db.scalar(
        select(func.max(HealthEvent.recorded_at)).where(
            HealthEvent.patient_id == patient.patient_id
        )
    )
    after_latest = (
        latest_recorded_at + timedelta(minutes=1)
        if latest_recorded_at is not None
        else utc_now()
    )
    return max(utc_now(), after_latest)


def _demo_events(db: Session, patient: ElderlyPatient) -> tuple[HealthEventCreate, ...]:
    start = _demo_start(db, patient)
    common = {
        "patient_id": patient.patient_id,
        "validation_status": ValidationStatus.VALID_REALTIME,
        "raw_payload": {"demo": True, "seed": "seed_demo_data.py", "version": 2},
    }
    return (
        HealthEventCreate(
            **common,
            external_event_id=f"{DEMO_EVENT_PREFIX}-hr-normal",
            metric_type=MetricType.HEART_RATE,
            numeric_value="78",
            metric_unit="BPM",
            recorded_at=start + DEMO_EVENT_OFFSETS["hr-normal"],
        ),
        HealthEventCreate(
            **common,
            external_event_id=f"{DEMO_EVENT_PREFIX}-hr-critical",
            metric_type=MetricType.HEART_RATE,
            numeric_value="154",
            metric_unit="BPM",
            recorded_at=start + DEMO_EVENT_OFFSETS["hr-critical"],
        ),
        HealthEventCreate(
            **common,
            external_event_id=f"{DEMO_EVENT_PREFIX}-spo2-normal",
            metric_type=MetricType.SPO2,
            numeric_value="97",
            metric_unit="%",
            recorded_at=start + DEMO_EVENT_OFFSETS["spo2-normal"],
        ),
        HealthEventCreate(
            **common,
            external_event_id=f"{DEMO_EVENT_PREFIX}-spo2-critical",
            metric_type=MetricType.SPO2,
            numeric_value="88",
            metric_unit="%",
            recorded_at=start + DEMO_EVENT_OFFSETS["spo2-critical"],
        ),
    )


def seed_demo_data(db: Session) -> DemoSeedResult:
    patient = _get_fixture_patient(db)

    # This is intentionally the same service used by the ingestion API. It
    # evaluates each event and lets the normal tracker/alert rules run.
    for event in _demo_events(db, patient):
        create_health_event(db, event)

    alerts = tuple(
        db.scalars(
            select(Alert)
            .where(
                Alert.patient_id == patient.patient_id,
                Alert.condition_key.in_(
                    (ConditionKey.HR_HIGH, ConditionKey.SPO2_LOW)
                ),
                Alert.status.in_((AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED)),
            )
            .order_by(Alert.confirmed_at, Alert.alert_id)
        ).all()
    )
    return DemoSeedResult(patient=patient, alerts=alerts)


def main() -> None:
    with get_session_factory()() as db:
        result = seed_demo_data(db)

    print(f"Demo patient ID: {result.patient.patient_id}")
    if not result.alerts:
        print("Resulting alerts: none")
        return
    summary = ", ".join(
        f"{alert.condition_key.value}={alert.severity.value}/{alert.status.value}"
        for alert in result.alerts
    )
    print(f"Resulting alerts: {summary}")


if __name__ == "__main__":
    main()
