import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.alert_actions.model import AlertAction, AlertActionType
from app.alerts.errors import AlertTransitionConflictError
from app.alerts.model import Alert, AlertStatus
from app.alerts.service import mark_false_alarm, resolve_alert
from app.condition_trackers.model import ConditionTracker
from app.core.config import Settings
from app.db.database import get_db
from app.event_evaluations.model import ConditionKey, EvaluationSeverity
from app.health_events.model import MetricType, ValidationStatus
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event
from app.main import create_app
from app.users.model import User, UserRole

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc) - timedelta(minutes=10)


@pytest.fixture()
def api_app(db_session):
    app = create_app(Settings(environment="testing", database_url=None))

    async def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    return app


def request(app, method, path, **kwargs):
    async def send():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


@pytest.fixture()
def actor(db_session):
    user = User(full_name="API Caregiver", role=UserRole.CAREGIVER)
    db_session.add(user)
    db_session.commit()
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
    return {"X-Alera-Actor-Id": str(actor.user_id)}


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


def test_get_alerts_empty_result(api_app):
    response = request(api_app, "GET", "/api/v1/alerts")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


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


def test_temporary_actor_header_validation(api_app, alert):
    path = f"/api/v1/alerts/{alert.alert_id}/acknowledge"
    assert request(api_app, "POST", path, json={}).status_code == 422
    assert request(
        api_app,
        "POST",
        path,
        headers={"X-Alera-Actor-Id": "not-a-uuid"},
        json={},
    ).status_code == 422
    assert request(
        api_app,
        "POST",
        path,
        headers={"X-Alera-Actor-Id": str(uuid4())},
        json={},
    ).status_code == 404


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
