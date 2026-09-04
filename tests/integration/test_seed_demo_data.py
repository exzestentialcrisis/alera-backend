from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.alerts.model import Alert, AlertStatus
from app.alerts.service import alert_display_payload, list_alerts
from app.event_evaluations.model import ConditionKey
from app.health_events.model import HealthEvent
from app.households.model import Household
from app.patients.model import ElderlyPatient, Sex
from app.users.model import User, UserRole
from scripts.seed_demo_data import (
    DEMO_EVENT_PREFIX,
    DEMO_HOUSEHOLD_CODE,
    DEMO_HOUSEHOLD_ID,
    DEMO_PATIENT_DISPLAY_NAME,
    DEMO_PATIENT_ID,
    seed_demo_data,
)

pytestmark = pytest.mark.integration


def install_auth_development_fixture(db_session):
    caregiver = User(
        user_id=UUID("11111111-1111-1111-1111-111111111111"),
        full_name="Alera Test Caregiver",
        role=UserRole.CAREGIVER,
    )
    patient_user = User(
        user_id=UUID("22222222-2222-2222-2222-222222222222"),
        full_name=DEMO_PATIENT_DISPLAY_NAME,
        role=UserRole.ELDERLY_PATIENT,
    )
    db_session.add_all([caregiver, patient_user])
    db_session.flush()
    household = Household(
        household_id=DEMO_HOUSEHOLD_ID,
        created_by_user_id=caregiver.user_id,
        household_name="Alera Test Household",
        household_code=DEMO_HOUSEHOLD_CODE,
    )
    db_session.add(household)
    db_session.flush()
    patient = ElderlyPatient(
        patient_id=DEMO_PATIENT_ID,
        user_id=patient_user.user_id,
        household_id=household.household_id,
        birthdate=date(1948, 6, 12),
        sex=Sex.FEMALE,
        normal_hr_min=60,
        normal_hr_max=100,
        usual_spo2_min=95,
    )
    db_session.add(patient)
    db_session.commit()


def test_seed_demo_data_is_idempotent_and_generates_display_ready_alerts(db_session):
    install_auth_development_fixture(db_session)
    first = seed_demo_data(db_session)
    first_alert_ids = {alert.alert_id for alert in first.alerts}

    second = seed_demo_data(db_session)

    assert second.patient.patient_id == first.patient.patient_id == DEMO_PATIENT_ID
    assert {alert.alert_id for alert in second.alerts} == first_alert_ids
    assert db_session.scalar(select(func.count(User.user_id))) == 2
    assert db_session.scalar(select(func.count(Household.household_id))) == 1
    assert db_session.scalar(select(func.count(ElderlyPatient.patient_id))) == 1
    assert db_session.scalar(select(func.count(HealthEvent.event_id))) == 4
    assert db_session.scalar(select(func.count(Alert.alert_id))) == 2
    assert db_session.scalar(
        select(func.count(HealthEvent.event_id)).where(
            HealthEvent.external_event_id.like(f"{DEMO_EVENT_PREFIX}-%")
        )
    ) == 4

    rows, total = list_alerts(
        db_session,
        patient_id=first.patient.patient_id,
        statuses=[AlertStatus.ACTIVE],
        severity=None,
        condition_key=None,
        limit=20,
        offset=0,
    )
    payloads = {
        alert.condition_key: alert_display_payload(
            alert,
            evaluation,
            event,
            patient,
            user,
        )
        for alert, evaluation, event, patient, user in rows
    }

    assert total == 2
    assert set(payloads) == {ConditionKey.HR_HIGH, ConditionKey.SPO2_LOW}
    assert payloads[ConditionKey.HR_HIGH]["patient_display_name"] == (
        DEMO_PATIENT_DISPLAY_NAME
    )
    assert payloads[ConditionKey.HR_HIGH]["reading_value"] == 154
    assert payloads[ConditionKey.SPO2_LOW]["patient_display_name"] == (
        DEMO_PATIENT_DISPLAY_NAME
    )
    assert payloads[ConditionKey.SPO2_LOW]["reading_value"] == 88


def test_seed_demo_data_fails_when_fixture_patient_is_missing(db_session):
    with pytest.raises(RuntimeError, match="fixture patient.*does not exist"):
        seed_demo_data(db_session)
