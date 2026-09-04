import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.alert_actions.model import AlertAction, AlertActionType
from app.auth.security import create_access_token
from app.alerts.errors import AlertTransitionConflictError
from app.alerts.model import Alert, AlertStatus
from app.alerts.service import mark_false_alarm, resolve_alert
from app.condition_trackers.model import ConditionTracker
from app.core.config import Settings
from app.db.database import get_db
from app.event_evaluations.model import (
    ConditionKey,
    EvaluationSeverity,
    EventEvaluation,
    MonitoringState,
)
from app.health_events.model import HealthEvent, MetricType, ValidationStatus
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event
from app.household_access.model import CaregiverPatientAssignment
from app.households.model import Household
from app.main import create_app
from app.patients.model import ElderlyPatient, Sex
from app.users.model import User, UserRole

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc) - timedelta(minutes=10)
JWT_SECRET = "alert-api-test-secret-that-is-long-and-random-enough"


@pytest.fixture()
def api_app(db_session, patient):
    app = create_app(
        Settings(
            environment="testing",
            database_url=None,
            alera_jwt_secret=JWT_SECRET,
        )
    )

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, household.created_by_user_id)
    token, _ = create_access_token(
        user_id=owner.user_id,
        household_id=household.household_id,
        secret=JWT_SECRET,
        expires_minutes=30,
    )
    app.state.test_alert_headers = {"Authorization": f"Bearer {token}"}
    return app


def request(app, method, path, **kwargs):
    if "headers" not in kwargs:
        kwargs["headers"] = app.state.test_alert_headers

    async def send():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


@pytest.fixture()
def actor(db_session, patient):
    user = User(full_name="API Caregiver", role=UserRole.CAREGIVER)
    db_session.add(user)
    db_session.flush()
    household = db_session.get(Household, patient.household_id)
    db_session.add(
        CaregiverPatientAssignment(
            caregiver_user_id=user.user_id,
            patient_id=patient.patient_id,
            assigned_by_user_id=household.created_by_user_id,
        )
    )
    db_session.commit()
    user._test_household_id = household.household_id
    return user


@pytest.fixture()
def alert(db_session, patient):
    item = Alert(
        patient_id=patient.patient_id,
        condition_key=ConditionKey.HR_HIGH,
        severity=EvaluationSeverity.WARNING,
        status=AlertStatus.ACTIVE,
        detected_at=NOW,
        confirmed_at=NOW + timedelta(minutes=5),
    )
    db_session.add(item)
    db_session.commit()
    return item


def actor_headers(actor):
    return jwt_headers(actor, actor._test_household_id)


def jwt_headers(actor, household_id):
    token, _ = create_access_token(
        user_id=actor.user_id,
        household_id=household_id,
        secret=JWT_SECRET,
        expires_minutes=30,
    )
    return {"Authorization": f"Bearer {token}"}


def test_alert_scope_for_assigned_and_unassigned_caregivers(
    api_app, db_session, patient, alert, actor
):
    assigned_headers = actor_headers(actor)
    assigned_list = request(
        api_app, "GET", "/api/v1/alerts", headers=assigned_headers
    )
    assert assigned_list.status_code == 200
    assert [item["alert_id"] for item in assigned_list.json()["items"]] == [
        str(alert.alert_id)
    ]
    assert request(
        api_app,
        "GET",
        f"/api/v1/alerts/{alert.alert_id}",
        headers=assigned_headers,
    ).status_code == 200

    unassigned = User(full_name="Unassigned Caregiver", role=UserRole.CAREGIVER)
    db_session.add(unassigned)
    db_session.commit()
    denied_headers = jwt_headers(unassigned, patient.household_id)
    denied_list = request(
        api_app, "GET", "/api/v1/alerts", headers=denied_headers
    )
    assert denied_list.status_code == 200
    assert denied_list.json()["items"] == []
    assert denied_list.json()["total"] == 0
    assert request(
        api_app,
        "GET",
        f"/api/v1/alerts/{alert.alert_id}",
        headers=denied_headers,
    ).status_code == 404
    assert request(
        api_app,
        "POST",
        f"/api/v1/alerts/{alert.alert_id}/acknowledge",
        headers=denied_headers,
        json={},
    ).status_code == 404


def test_care_admin_alert_scope_is_limited_to_owned_households(
    api_app, db_session, patient, alert
):
    owned_household = db_session.get(Household, patient.household_id)
    owner = db_session.get(User, owned_household.created_by_user_id)
    other_admin = User(full_name="Other Admin", role=UserRole.CARE_ADMIN)
    other_patient_user = User(
        full_name="Other Patient", role=UserRole.ELDERLY_PATIENT
    )
    db_session.add_all([other_admin, other_patient_user])
    db_session.flush()
    other_household = Household(
        created_by_user_id=other_admin.user_id,
        household_name="Other Household",
    )
    db_session.add(other_household)
    db_session.flush()
    other_patient = ElderlyPatient(
        user_id=other_patient_user.user_id,
        household_id=other_household.household_id,
        birthdate=datetime(1950, 1, 1).date(),
        sex=Sex.OTHER,
    )
    db_session.add(other_patient)
    db_session.flush()
    other_alert = make_alert(db_session, other_patient)
    db_session.commit()

    owner_headers = jwt_headers(owner, owned_household.household_id)
    response = request(api_app, "GET", "/api/v1/alerts", headers=owner_headers)
    assert response.status_code == 200
    assert {item["alert_id"] for item in response.json()["items"]} == {
        str(alert.alert_id)
    }
    assert request(
        api_app,
        "GET",
        f"/api/v1/alerts/{other_alert.alert_id}",
        headers=owner_headers,
    ).status_code == 404


def make_alert(db, patient, **changes):
    values = {
        "patient_id": patient.patient_id,
        "condition_key": ConditionKey.HR_HIGH,
        "severity": EvaluationSeverity.WARNING,
        "status": AlertStatus.ACTIVE,
        "detected_at": NOW,
        "confirmed_at": NOW,
    }
    values.update(changes)
    item = Alert(**values)
    db.add(item)
    db.flush()
    return item


def add_trigger(db, alert, *, metric_type, reading, unit, threshold, reason):
    event = HealthEvent(
        patient_id=alert.patient_id,
        metric_type=metric_type,
        numeric_value=reading,
        metric_unit=unit,
        recorded_at=alert.confirmed_at,
        validation_status=ValidationStatus.VALID_REALTIME,
        raw_payload={},
    )
    db.add(event)
    db.flush()
    evaluation = EventEvaluation(
        event_id=event.event_id,
        alert_id=alert.alert_id,
        condition_key=alert.condition_key,
        threshold_value_used=threshold,
        threshold_met=True,
        persistence_met=True,
        previous_state=MonitoringState.ELEVATED,
        new_state=MonitoringState.WARNING,
        severity=alert.severity,
        evaluation_reason=reason,
        evaluated_at=alert.confirmed_at,
    )
    db.add(evaluation)
    return event, evaluation


def test_get_alerts_empty_result(api_app):
    response = request(api_app, "GET", "/api/v1/alerts")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_get_alerts_includes_hr_and_spo2_display_data(
    api_app,
    db_session,
    patient,
):
    patient.nickname = "Nana"
    hr_alert = make_alert(
        db_session,
        patient,
        condition_key=ConditionKey.HR_HIGH,
        confirmed_at=NOW + timedelta(minutes=2),
    )
    add_trigger(
        db_session,
        hr_alert,
        metric_type=MetricType.HEART_RATE,
        reading=Decimal("121"),
        unit="beats/minute",
        threshold=Decimal("100"),
        reason="Heart rate exceeded the configured maximum.",
    )
    spo2_alert = make_alert(
        db_session,
        patient,
        condition_key=ConditionKey.SPO2_LOW,
        severity=EvaluationSeverity.CRITICAL,
        confirmed_at=NOW + timedelta(minutes=1),
    )
    add_trigger(
        db_session,
        spo2_alert,
        metric_type=MetricType.SPO2,
        reading=Decimal("88"),
        unit="percent",
        threshold=Decimal("95"),
        reason="SpO2 was below the configured minimum.",
    )
    db_session.commit()

    response = request(api_app, "GET", "/api/v1/alerts")

    assert response.status_code == 200
    items = {item["condition_key"]: item for item in response.json()["items"]}
    expected_hr = {
        "patient_display_name": "Nana",
        "condition_key": "HR_HIGH",
        "metric_type": "HEART_RATE",
        "title": "High Heart Rate",
        "reading_value": "121.00",
        "reading_unit": "BPM",
        "threshold_value": "100.00",
        "threshold_unit": "BPM",
        "evaluation_reason": "Heart rate exceeded the configured maximum.",
    }
    assert {key: items["HR_HIGH"][key] for key in expected_hr} == expected_hr
    assert items["SPO2_LOW"]["metric_type"] == "SPO2"
    assert items["SPO2_LOW"]["title"] == "Low SpO₂"
    assert items["SPO2_LOW"]["reading_value"] == "88.00"
    assert items["SPO2_LOW"]["reading_unit"] == "%"
    assert items["SPO2_LOW"]["threshold_value"] == "95.00"
    assert items["SPO2_LOW"]["threshold_unit"] == "%"
    assert items["SPO2_LOW"]["evaluation_reason"] == (
        "SpO2 was below the configured minimum."
    )
    detail = request(
        api_app,
        "GET",
        f"/api/v1/alerts/{hr_alert.alert_id}",
    ).json()
    for field in expected_hr:
        assert detail[field] == items["HR_HIGH"][field]


def test_alerts_without_triggering_data_serialize_nulls(
    api_app,
    db_session,
    alert,
):
    list_response = request(api_app, "GET", "/api/v1/alerts")
    detail_response = request(api_app, "GET", f"/api/v1/alerts/{alert.alert_id}")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    for body in (list_response.json()["items"][0], detail_response.json()):
        assert body["patient_display_name"] == "Test Patient"
        assert body["reading_value"] is None
        assert body["threshold_value"] is None
        assert body["evaluation_reason"] is None
    assert detail_response.json()["triggering_event"] is None
    assert detail_response.json()["triggering_evaluation"] is None


def test_get_alerts_filters_paginates_and_orders(
    api_app,
    db_session,
    patient,
):
    terminal = make_alert(
        db_session,
        patient,
        status=AlertStatus.RESOLVED,
        resolved_at=NOW + timedelta(minutes=2),
        confirmed_at=NOW + timedelta(minutes=1),
    )
    warning = make_alert(
        db_session,
        patient,
        condition_key=ConditionKey.HR_LOW,
        confirmed_at=NOW + timedelta(minutes=3),
    )
    critical = make_alert(
        db_session,
        patient,
        condition_key=ConditionKey.SPO2_LOW,
        severity=EvaluationSeverity.CRITICAL,
        confirmed_at=NOW + timedelta(minutes=2),
    )
    db_session.commit()

    response = request(
        api_app,
        "GET",
        "/api/v1/alerts",
        params={"patient_id": str(patient.patient_id), "limit": 2, "offset": 0},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 3
    assert [item["alert_id"] for item in body["items"]] == [
        str(critical.alert_id),
        str(warning.alert_id),
    ]
    second_page = request(
        api_app,
        "GET",
        "/api/v1/alerts",
        params={"limit": 2, "offset": 2},
    ).json()
    assert second_page["items"][0]["alert_id"] == str(terminal.alert_id)

    terminal_response = request(
        api_app,
        "GET",
        "/api/v1/alerts",
        params=[("status", "RESOLVED"), ("status", "FALSE_ALARM")],
    )
    assert terminal_response.json()["total"] == 1
    assert terminal_response.json()["items"][0]["alert_id"] == str(terminal.alert_id)


@pytest.mark.parametrize(
    "query",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}],
)
def test_get_alerts_rejects_invalid_pagination(api_app, query):
    assert request(
        api_app,
        "GET",
        "/api/v1/alerts",
        params=query,
    ).status_code == 422


def test_alert_detail_includes_trigger_and_latest_action(
    api_app,
    db_session,
    patient,
    actor,
):
    event = create_health_event(
        db_session,
        HealthEventCreate(
            patient_id=patient.patient_id,
            metric_type=MetricType.HEART_RATE,
            numeric_value="151",
            recorded_at=NOW,
            validation_status=ValidationStatus.VALID_REALTIME,
        ),
    )
    alert = db_session.scalar(select(Alert))
    note_response = request(
        api_app,
        "POST",
        f"/api/v1/alerts/{alert.alert_id}/notes",
        headers=actor_headers(actor),
        json={"note": "Latest note"},
    )
    assert note_response.status_code == 200

    response = request(api_app, "GET", f"/api/v1/alerts/{alert.alert_id}")
    body = response.json()
    assert response.status_code == 200
    assert body["triggering_event"]["event_id"] == str(event.event_id)
    assert body["triggering_evaluation"]["alert_id"] == str(alert.alert_id)
    assert body["latest_action"]["action_note"] == "Latest note"


def test_alert_detail_missing_returns_404(api_app):
    assert request(
        api_app,
        "GET",
        f"/api/v1/alerts/{uuid4()}",
    ).status_code == 404


def test_action_history_empty_ordered_and_missing(
    api_app,
    db_session,
    alert,
    actor,
):
    empty = request(api_app, "GET", f"/api/v1/alerts/{alert.alert_id}/actions")
    assert empty.json() == {"items": []}
    first = AlertAction(
        alert_id=alert.alert_id,
        performed_by_user_id=actor.user_id,
        action_type=AlertActionType.ADD_NOTE,
        action_note="First",
        previous_status=alert.status,
        new_status=alert.status,
        performed_at=NOW,
    )
    second = AlertAction(
        alert_id=alert.alert_id,
        performed_by_user_id=actor.user_id,
        action_type=AlertActionType.ADD_NOTE,
        action_note="Second",
        previous_status=alert.status,
        new_status=alert.status,
        performed_at=NOW + timedelta(seconds=1),
    )
    db_session.add_all([second, first])
    db_session.commit()

    history = request(api_app, "GET", f"/api/v1/alerts/{alert.alert_id}/actions")
    assert [item["action_note"] for item in history.json()["items"]] == [
        "First",
        "Second",
    ]
    assert request(
        api_app,
        "GET",
        f"/api/v1/alerts/{uuid4()}/actions",
    ).status_code == 404


def test_bearer_token_validation_and_legacy_header_is_not_authority(
    api_app, alert, actor
):
    path = f"/api/v1/alerts/{alert.alert_id}/acknowledge"
    assert request(
        api_app, "GET", "/api/v1/alerts", headers={}
    ).status_code == 401
    assert request(api_app, "POST", path, headers={}, json={}).status_code == 401
    assert request(
        api_app,
        "GET",
        "/api/v1/alerts",
        headers={"Authorization": "Bearer not-a-token"},
    ).status_code == 401
    expired, _ = create_access_token(
        user_id=actor.user_id,
        household_id=actor._test_household_id,
        secret=JWT_SECRET,
        expires_minutes=1,
        now=datetime.now(timezone.utc) - timedelta(minutes=2),
    )
    assert request(
        api_app,
        "GET",
        "/api/v1/alerts",
        headers={"Authorization": f"Bearer {expired}"},
    ).status_code == 401
    assert request(
        api_app,
        "POST",
        path,
        headers={"X-Alera-Actor-Id": str(actor.user_id)},
        json={},
    ).status_code == 401


def test_action_on_missing_alert_returns_404(api_app, actor):
    assert request(
        api_app,
        "POST",
        f"/api/v1/alerts/{uuid4()}/acknowledge",
        headers=actor_headers(actor),
        json={},
    ).status_code == 404


def test_acknowledge_is_atomic_and_idempotent(
    api_app,
    db_session,
    alert,
    actor,
):
    path = f"/api/v1/alerts/{alert.alert_id}/acknowledge"
    first = request(
        api_app,
        "POST",
        path,
        headers=actor_headers(actor),
        json={"note": "Taking ownership"},
    )
    second = request(
        api_app,
        "POST",
        path,
        headers=actor_headers(actor),
        json={},
    )

    assert first.status_code == 200
    assert first.json()["alert"]["status"] == "ACKNOWLEDGED"
    assert first.json()["action"]["action_type"] == "ACKNOWLEDGE"
    assert first.json()["idempotent"] is False
    assert second.json()["action"] is None
    assert second.json()["idempotent"] is True
    assert db_session.scalar(select(func.count(AlertAction.alert_action_id))) == 1


@pytest.mark.parametrize("starting_status", [AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED])
def test_resolve_sets_workflow_timestamp_and_is_idempotent(
    api_app,
    db_session,
    alert,
    actor,
    starting_status,
):
    alert.status = starting_status
    db_session.commit()
    path = f"/api/v1/alerts/{alert.alert_id}/resolve"
    first = request(
        api_app,
        "POST",
        path,
        headers=actor_headers(actor),
        json={"note": "Handled"},
    )
    second = request(
        api_app,
        "POST",
        path,
        headers=actor_headers(actor),
        json={},
    )

    assert first.json()["alert"]["status"] == "RESOLVED"
    assert first.json()["alert"]["resolved_at"] is not None
    assert first.json()["action"]["action_type"] == "RESOLVE"
    assert second.json()["idempotent"] is True


@pytest.mark.parametrize("starting_status", [AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED])
def test_false_alarm_requires_reason_and_is_idempotent(
    api_app,
    db_session,
    alert,
    actor,
    starting_status,
):
    alert.status = starting_status
    db_session.commit()
    path = f"/api/v1/alerts/{alert.alert_id}/false-alarm"
    assert request(
        api_app,
        "POST",
        path,
        headers=actor_headers(actor),
        json={"reason": "  "},
    ).status_code == 422
    first = request(
        api_app,
        "POST",
        path,
        headers=actor_headers(actor),
        json={"reason": "Sensor was detached"},
    )
    second = request(
        api_app,
        "POST",
        path,
        headers=actor_headers(actor),
        json={"reason": "Sensor was detached"},
    )

    assert first.json()["alert"]["status"] == "FALSE_ALARM"
    assert first.json()["action"]["action_type"] == "MARK_FALSE_ALARM"
    assert second.json()["idempotent"] is True


@pytest.mark.parametrize(
    ("starting_status", "endpoint"),
    [
        (AlertStatus.RESOLVED, "acknowledge"),
        (AlertStatus.FALSE_ALARM, "resolve"),
        (AlertStatus.RESOLVED, "false-alarm"),
        (AlertStatus.ARCHIVED, "acknowledge"),
    ],
)
def test_invalid_lifecycle_transitions_return_409(
    api_app,
    db_session,
    alert,
    actor,
    starting_status,
    endpoint,
):
    alert.status = starting_status
    if starting_status in (AlertStatus.RESOLVED, AlertStatus.FALSE_ALARM):
        alert.resolved_at = alert.confirmed_at
    db_session.commit()
    body = {"reason": "Incorrect reading"} if endpoint == "false-alarm" else {}

    assert request(
        api_app,
        "POST",
        f"/api/v1/alerts/{alert.alert_id}/{endpoint}",
        headers=actor_headers(actor),
        json=body,
    ).status_code == 409


def test_notes_are_non_idempotent_and_preserve_terminal_status(
    api_app,
    db_session,
    alert,
    actor,
):
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = alert.confirmed_at
    db_session.commit()
    path = f"/api/v1/alerts/{alert.alert_id}/notes"
    for _ in range(2):
        response = request(
            api_app,
            "POST",
            path,
            headers=actor_headers(actor),
            json={"note": "Same note"},
        )
        assert response.status_code == 200
        assert response.json()["alert"]["status"] == "RESOLVED"
        assert response.json()["idempotent"] is False

    assert db_session.scalar(select(func.count(AlertAction.alert_action_id))) == 2


def test_intervention_records_structured_metadata_without_status_change(
    api_app,
    alert,
    actor,
):
    response = request(
        api_app,
        "POST",
        f"/api/v1/alerts/{alert.alert_id}/interventions",
        headers=actor_headers(actor),
        json={
            "intervention_type": "PATIENT_CHECK",
            "note": "Patient was seated and monitored.",
        },
    )
    body = response.json()

    assert response.status_code == 200
    assert body["alert"]["status"] == "ACTIVE"
    assert body["action"]["action_type"] == "LOG_INTERVENTION"
    assert body["action"]["action_metadata"] == {
        "intervention_type": "PATIENT_CHECK"
    }
    assert request(
        api_app,
        "POST",
        f"/api/v1/alerts/{alert.alert_id}/interventions",
        headers=actor_headers(actor),
        json={"intervention_type": "INVALID", "note": "Something"},
    ).status_code == 422


@pytest.mark.parametrize("endpoint", ["notes", "interventions"])
def test_archived_alert_rejects_actions(
    api_app,
    db_session,
    alert,
    actor,
    endpoint,
):
    alert.status = AlertStatus.ARCHIVED
    db_session.commit()
    body = (
        {"note": "No longer mutable"}
        if endpoint == "notes"
        else {"intervention_type": "OTHER", "note": "No longer mutable"}
    )
    assert request(
        api_app,
        "POST",
        f"/api/v1/alerts/{alert.alert_id}/{endpoint}",
        headers=actor_headers(actor),
        json=body,
    ).status_code == 409


@pytest.mark.parametrize(
    ("endpoint", "body", "expected_status"),
    [
        ("acknowledge", {}, AlertStatus.ACKNOWLEDGED),
        ("resolve", {}, AlertStatus.RESOLVED),
        (
            "false-alarm",
            {"reason": "Sensor placement was invalid"},
            AlertStatus.FALSE_ALARM,
        ),
    ],
)
def test_lifecycle_actions_do_not_mutate_tracker(
    api_app,
    db_session,
    patient,
    actor,
    endpoint,
    body,
    expected_status,
):
    event = create_health_event(
        db_session,
        HealthEventCreate(
            patient_id=patient.patient_id,
            metric_type=MetricType.HEART_RATE,
            numeric_value="151",
            recorded_at=NOW,
            validation_status=ValidationStatus.VALID_REALTIME,
        ),
    )
    alert = db_session.scalar(select(Alert))
    tracker = db_session.scalar(select(ConditionTracker))
    snapshot = (
        tracker.active,
        tracker.started_at,
        tracker.last_seen_at,
        tracker.last_event_id,
        tracker.consecutive_event_count,
    )

    response = request(
        api_app,
        "POST",
        f"/api/v1/alerts/{alert.alert_id}/{endpoint}",
        headers=actor_headers(actor),
        json=body,
    )
    db_session.refresh(tracker)

    assert response.status_code == 200
    assert response.json()["alert"]["status"] == expected_status.value
    assert event.event_id == tracker.last_event_id
    assert snapshot == (
        tracker.active,
        tracker.started_at,
        tracker.last_seen_at,
        tracker.last_event_id,
        tracker.consecutive_event_count,
    )


def test_competing_terminal_transitions_only_one_succeeds(
    integration_engine,
    db_session,
    patient,
):
    alert = make_alert(db_session, patient)
    first_actor = User(full_name="Resolver", role=UserRole.CAREGIVER)
    second_actor = User(full_name="Reviewer", role=UserRole.CAREGIVER)
    db_session.add_all([first_actor, second_actor])
    db_session.flush()
    household = db_session.get(Household, patient.household_id)
    db_session.add_all(
        [
            CaregiverPatientAssignment(
                caregiver_user_id=worker_actor.user_id,
                patient_id=patient.patient_id,
                assigned_by_user_id=household.created_by_user_id,
            )
            for worker_actor in (first_actor, second_actor)
        ]
    )
    db_session.commit()
    barrier = Barrier(2)
    factory = sessionmaker(bind=integration_engine, expire_on_commit=False)

    def worker(operation, actor_id):
        session = factory()
        try:
            actor = session.get(User, actor_id)
            barrier.wait()
            operation(session, alert.alert_id, actor)
            session.commit()
            return "success"
        except AlertTransitionConflictError:
            session.rollback()
            return "conflict"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: worker(*item),
                [
                    (
                        lambda session, alert_id, actor: resolve_alert(
                            session, alert_id, actor, None
                        ),
                        first_actor.user_id,
                    ),
                    (
                        lambda session, alert_id, actor: mark_false_alarm(
                            session, alert_id, actor, "Not genuine"
                        ),
                        second_actor.user_id,
                    ),
                ],
            )
        )

    db_session.expire_all()
    assert sorted(results) == ["conflict", "success"]
    assert db_session.get(Alert, alert.alert_id).status in {
        AlertStatus.RESOLVED,
        AlertStatus.FALSE_ALARM,
    }
    assert db_session.scalar(select(func.count(AlertAction.alert_action_id))) == 1
