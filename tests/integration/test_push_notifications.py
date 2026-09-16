import asyncio
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.alerts.model import Alert, AlertStatus
from app.auth.security import create_access_token
from app.core.config import Settings
from app.core.time import utc_now
from app.db.database import get_db
from app.devices.model import CaregiverPushDevice
from app.event_evaluations.model import EventEvaluation
from app.health_events.schema import HealthEventCreate
from app.health_events.service import create_health_event
from app.household_access.model import CaregiverPatientAssignment
from app.main import create_app
from app.notifications import events, service
from app.notifications.fcm import FCMSender
from app.users.model import AccountStatus, User, UserRole

pytestmark = pytest.mark.integration
SECRET = "test-only-device-jwt-secret"
PATH = "/api/v1/devices/fcm-token"


def request(app, method, payload=None, headers=None):
    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, PATH, json=payload, headers=headers)

    return asyncio.run(send())


@pytest.fixture
def api(db_session):
    app = create_app(Settings(_env_file=None, alera_jwt_secret=SECRET))

    def db_override():
        yield db_session

    app.dependency_overrides[get_db] = db_override
    return app


def caregiver(db, patient, *, assigned=True, active=True):
    user = User(
        full_name="Push Caregiver",
        role=UserRole.CAREGIVER,
        account_status=AccountStatus.ACTIVE if active else AccountStatus.DISABLED,
    )
    db.add(user)
    db.flush()
    if assigned:
        db.add(
            CaregiverPatientAssignment(
                caregiver_user_id=user.user_id,
                patient_id=patient.patient_id,
                assigned_by_user_id=user.user_id,
            )
        )
    db.commit()
    return user


def headers(user, patient):
    token, _ = create_access_token(
        user_id=user.user_id,
        household_id=patient.household_id,
        secret=SECRET,
        expires_minutes=30,
    )
    return {"Authorization": f"Bearer {token}"}


def evaluation_for(db, event):
    return db.scalar(
        select(EventEvaluation).where(EventEvaluation.event_id == event.event_id)
    )


def test_registration_reassignment_and_ownership_delete(api, db_session, patient):
    first = caregiver(db_session, patient)
    second = caregiver(db_session, patient)
    payload = {"token": "synthetic-device:token", "platform": "ANDROID"}
    assert request(api, "POST", payload).status_code == 401
    assert request(api, "POST", payload, headers(first, patient)).json() == {
        "status": "ok"
    }
    device = db_session.scalar(select(CaregiverPushDevice))
    original_id, created, last_seen = device.id, device.created_at, device.last_seen_at
    assert device.user_id == first.user_id
    assert request(api, "POST", payload, headers(first, patient)).status_code == 200
    db_session.refresh(device)
    assert device.last_seen_at >= last_seen
    assert request(api, "POST", payload, headers(second, patient)).status_code == 200
    db_session.refresh(device)
    assert device.user_id == second.user_id
    assert device.id == original_id and device.created_at == created
    assert len(db_session.scalars(select(CaregiverPushDevice)).all()) == 1
    assert request(
        api, "DELETE", {"token": payload["token"]}, headers(first, patient)
    ).json() == {"status": "ok"}
    assert db_session.scalar(select(CaregiverPushDevice.id)) == original_id
    assert request(
        api, "DELETE", {"token": payload["token"]}, headers(second, patient)
    ).json() == {"status": "ok"}
    assert db_session.scalar(select(CaregiverPushDevice.id)) is None
    assert request(
        api, "DELETE", {"token": payload["token"]}, headers(second, patient)
    ).json() == {"status": "ok"}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"token": "", "platform": "ANDROID"},
        {"token": " secret ", "platform": "ANDROID"},
        {"token": "a" * 2049, "platform": "ANDROID"},
        {"token": 123, "platform": "ANDROID"},
        {"token": "synthetic", "platform": "IOS"},
        {"token": "synthetic", "platform": "ANDROID", "user_id": "other"},
        {"token": "bad\nvalue", "platform": "ANDROID"},
    ],
)
def test_invalid_registration_is_generic(api, db_session, patient, payload):
    actor = caregiver(db_session, patient)
    response = request(api, "POST", payload, headers(actor, patient))
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid device token request."}
    assert db_session.scalar(select(CaregiverPushDevice.id)) is None


def test_delete_validation_and_role_auth(api, db_session, patient):
    actor = caregiver(db_session, patient)
    assert request(
        api, "DELETE", {"token": "bad token"}, headers(actor, patient)
    ).json() == {"detail": "Invalid device token request."}
    patient_user = db_session.get(User, patient.user_id)
    assert (
        request(
            api,
            "POST",
            {"token": "synthetic", "platform": "ANDROID"},
            headers(patient_user, patient),
        ).status_code
        == 403
    )
    actor.account_status = AccountStatus.DISABLED
    db_session.commit()
    assert (
        request(
            api, "DELETE", {"token": "synthetic"}, headers(actor, patient)
        ).status_code
        == 401
    )


@pytest.fixture
def transport(monkeypatch):
    settings = Settings(
        _env_file=None,
        fcm_enabled=True,
        firebase_project_id="alera-test",
        firebase_service_account_json='{"project_id":"alera-test","client_email":"synthetic","private_key":"synthetic"}',
    )
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(FCMSender, "_access_token", lambda self: "mock-oauth")
    post = Mock(return_value=httpx.Response(200, json={"name": "mock-message"}))
    monkeypatch.setattr("app.notifications.fcm.httpx.post", post)
    return post


def device(db, user, token):
    result = CaregiverPushDevice(
        user_id=user.user_id, fcm_token=token, platform="ANDROID"
    )
    db.add(result)
    db.commit()
    return result


def critical(db, payload):
    create_health_event(
        db,
        HealthEventCreate(
            **{
                **payload,
                "external_event_id": str(uuid4()),
                "numeric_value": "151",
                "recorded_at": payload["recorded_at"] - timedelta(seconds=15),
            }
        ),
    )
    return create_health_event(
        db, HealthEventCreate(**{**payload, "numeric_value": "151"})
    )


def test_only_active_assigned_devices_and_exact_payload(
    db_session, patient, event_payload, transport
):
    assigned = caregiver(db_session, patient)
    device(db_session, assigned, "assigned-one")
    device(db_session, assigned, "assigned-two")
    device(db_session, caregiver(db_session, patient, assigned=False), "unassigned")
    device(db_session, caregiver(db_session, patient, active=False), "disabled")
    former = caregiver(db_session, patient)
    assignment = db_session.scalar(
        select(CaregiverPatientAssignment).where(
            CaregiverPatientAssignment.caregiver_user_id == former.user_id
        )
    )
    assignment.unassigned_at = utc_now()
    device(db_session, former, "former")

    def committed_response(*args, **kwargs):
        with Session(db_session.get_bind()) as independent:
            assert (
                independent.get(Alert, kwargs["json"]["message"]["data"]["alert_id"])
                is not None
            )
        return httpx.Response(200, json={})

    transport.side_effect = committed_response
    critical(db_session, event_payload)
    alert = db_session.scalar(select(Alert))
    assert transport.call_count == 2
    messages = [call.kwargs["json"]["message"] for call in transport.call_args_list]
    assert {message["token"] for message in messages} == {
        "assigned-one",
        "assigned-two",
    }
    for message in messages:
        assert message == {
            "token": message["token"],
            "notification": {
                "title": "Critical: High Heart Rate",
                "body": "Test Patient • 151 BPM",
            },
            "data": {
                "type": "ALERT",
                "alert_id": str(alert.alert_id),
                "patient_id": str(patient.patient_id),
            },
        }
    critical(db_session, event_payload)  # Idempotent ingestion.
    critical(db_session, {**event_payload, "external_event_id": str(uuid4())})
    db_session.scalars(select(Alert)).all()
    db_session.commit()
    assert transport.call_count == 2


@pytest.mark.parametrize(
    "mode", ["disabled", "missing", "malformed", "network", "hook"]
)
def test_delivery_failure_never_prevents_persistence(
    db_session, patient, event_payload, monkeypatch, transport, mode
):
    device(db_session, caregiver(db_session, patient), "synthetic")
    if mode in {"disabled", "missing", "malformed"}:
        settings = Settings(
            _env_file=None,
            fcm_enabled=mode != "disabled",
            firebase_project_id="alera-test" if mode == "malformed" else None,
            firebase_service_account_json="not-json" if mode == "malformed" else None,
        )
        monkeypatch.setattr(service, "get_settings", lambda: settings)
        if mode == "malformed":
            monkeypatch.setattr(
                FCMSender,
                "_access_token",
                lambda self: (_ for _ in ()).throw(ValueError("malformed")),
            )
    if mode == "network":
        transport.side_effect = httpx.ConnectError("synthetic failure")
    if mode == "hook":
        monkeypatch.setattr(
            events,
            "deliver_alert_notifications",
            Mock(side_effect=RuntimeError("failure")),
        )
    result = critical(db_session, event_payload)
    with Session(db_session.get_bind()) as independent:
        assert independent.get(type(result), result.event_id) is not None
        assert independent.scalar(select(Alert)) is not None
    if mode in {"disabled", "missing", "malformed", "hook"}:
        transport.assert_not_called()


def test_unregistered_cleanup(db_session, patient, event_payload, transport):
    device(db_session, caregiver(db_session, patient), "invalid-synthetic")
    transport.return_value = httpx.Response(
        404,
        json={
            "error": {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
                        "errorCode": "UNREGISTERED",
                    }
                ]
            }
        },
    )
    critical(db_session, event_payload)
    assert db_session.scalar(select(CaregiverPushDevice.id)) is None
    assert db_session.scalar(select(Alert)) is not None


def test_rollback_and_savepoint_do_not_send(
    db_session, patient, event_payload, transport
):
    from app.event_evaluations.service import evaluate_event
    from app.health_events.model import HealthEvent

    device(db_session, caregiver(db_session, patient), "synthetic")

    def evaluate():
        for offset in (-15, 0):
            event = HealthEvent(
                **HealthEventCreate(
                    **{
                        **event_payload,
                        "external_event_id": str(uuid4()),
                        "numeric_value": "151",
                        "recorded_at": event_payload["recorded_at"]
                        + timedelta(seconds=offset),
                    }
                ).model_dump()
            )
            db_session.add(event)
            db_session.flush()
            evaluate_event(db_session, event)

    evaluate()
    transport.assert_not_called()
    db_session.rollback()
    db_session.commit()
    transport.assert_not_called()
    assert db_session.scalar(select(Alert)) is None
    nested = db_session.begin_nested()
    evaluate()
    nested.rollback()
    db_session.commit()
    transport.assert_not_called()
    nested = db_session.begin_nested()
    evaluate()
    nested.commit()
    transport.assert_not_called()
    db_session.commit()
    assert transport.call_count == 1


@pytest.mark.parametrize(
    "metric,value,offsets,critical_value",
    [
        ("HEART_RATE", "110", tuple(range(0, 121, 15)), "151"),
        ("SPO2", "93", (0, 60), "85"),
    ],
)
def test_warning_qualification_and_critical_escalation_each_send_once(
    db_session, patient, event_payload, transport,
    metric, value, offsets, critical_value,
):
    device(db_session, caregiver(db_session, patient), "synthetic")

    def ingest(reading, offset):
        create_health_event(
            db_session,
            HealthEventCreate(**{
                **event_payload,
                "metric_type": metric,
                "metric_unit": "bpm" if metric == "HEART_RATE" else "%",
                "numeric_value": reading,
                "external_event_id": str(uuid4()),
                "recorded_at": event_payload["recorded_at"] + timedelta(seconds=offset),
            }),
        )

    for offset in offsets[:-1]:
        ingest(value, offset)
        transport.assert_not_called()
    ingest(value, offsets[-1])
    assert transport.call_count == 1
    ingest(critical_value, offsets[-1] + 30)
    assert transport.call_count == 1
    ingest(critical_value, offsets[-1] + 45)
    assert transport.call_count == 2
    escalation = transport.call_args.kwargs["json"]["message"]
    unit = "BPM" if metric == "HEART_RATE" else "%"
    separator = " " if metric == "HEART_RATE" else ""
    assert escalation["notification"] == {
        "title": (
            "Critical: High Heart Rate"
            if metric == "HEART_RATE"
            else "Critical: Low SpO₂"
        ),
        "body": f"Test Patient • {critical_value}{separator}{unit}",
    }
    ingest(critical_value, offsets[-1] + 75)
    assert transport.call_count == 2
    assert len(db_session.scalars(select(Alert)).all()) == 1


def test_acknowledged_warning_still_sends_critical_escalation(
    db_session, patient, event_payload, transport,
):
    device(db_session, caregiver(db_session, patient), "synthetic")

    def ingest(reading, offset):
        create_health_event(
            db_session,
            HealthEventCreate(**{
                **event_payload,
                "numeric_value": reading,
                "external_event_id": str(uuid4()),
                "recorded_at": event_payload["recorded_at"] + timedelta(seconds=offset),
            }),
        )

    for offset in range(0, 121, 15):
        ingest("110", offset)
    alert = db_session.scalar(select(Alert))
    assert transport.call_count == 1

    alert.status = AlertStatus.ACKNOWLEDGED
    db_session.commit()
    ingest("151", 150)
    assert transport.call_count == 1
    ingest("151", 165)

    db_session.refresh(alert)
    assert transport.call_count == 2
    assert alert.status is AlertStatus.ACKNOWLEDGED
    assert alert.severity.value == "CRITICAL"
    assert transport.call_args.kwargs["json"]["message"]["notification"] == {
        "title": "Critical: High Heart Rate",
        "body": "Test Patient • 151 BPM",
    }


def test_new_critical_occurrence_sends_again_while_prior_alert_is_unresolved(
    db_session, patient, event_payload, transport,
):
    device(db_session, caregiver(db_session, patient), "synthetic")

    def ingest(reading, offset):
        return create_health_event(
            db_session,
            HealthEventCreate(**{
                **event_payload,
                "numeric_value": reading,
                "external_event_id": str(uuid4()),
                "recorded_at": event_payload["recorded_at"]
                + timedelta(seconds=offset),
            }),
        )

    ingest("151", 0)
    first = ingest("155", 15)
    first_alert_id = evaluation_for(db_session, first).alert_id
    assert transport.call_count == 1

    for offset in range(30, 121, 15):
        ingest("78", offset)
    ingest("160", 135)
    recurring = ingest("165", 150)
    recurring_alert_id = evaluation_for(db_session, recurring).alert_id

    assert transport.call_count == 2
    assert recurring_alert_id != first_alert_id
    assert len(db_session.scalars(select(Alert)).all()) == 2
    assert transport.call_args.kwargs["json"]["message"]["notification"] == {
        "title": "Critical: High Heart Rate",
        "body": "Test Patient • 165 BPM",
    }


def test_invalid_cleanup_preserves_concurrently_refreshed_registration(
    db_session, patient, event_payload, transport,
):
    actor = caregiver(db_session, patient)
    registered = device(db_session, actor, "synthetic")
    registration_id = registered.id

    def refresh_during_send(*args, **kwargs):
        with Session(db_session.get_bind()) as independent:
            independent.execute(
                update(CaregiverPushDevice)
                .where(CaregiverPushDevice.id == registration_id)
                .values(updated_at=utc_now(), last_seen_at=utc_now())
            )
            independent.commit()
        return httpx.Response(404, json={"error": {"details": [{
            "@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError",
            "errorCode": "UNREGISTERED",
        }]}})

    transport.side_effect = refresh_during_send
    critical(db_session, event_payload)
    assert db_session.scalar(select(CaregiverPushDevice.id)) == registration_id


def test_alert_api_reads_and_actions_do_not_send(
    api, db_session, patient, event_payload, transport,
):
    actor = caregiver(db_session, patient)
    device(db_session, actor, "synthetic")
    critical(db_session, event_payload)
    alert = db_session.scalar(select(Alert))
    assert transport.call_count == 1

    async def read_and_acknowledge():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api), base_url="http://test",
            headers=headers(actor, patient),
        ) as client:
            assert (await client.get("/api/v1/alerts")).status_code == 200
            path = f"/api/v1/alerts/{alert.alert_id}"
            assert (await client.get(path)).status_code == 200
            assert (await client.post(f"{path}/acknowledge", json={})).status_code == 200

    asyncio.run(read_and_acknowledge())
    assert transport.call_count == 1


def test_only_active_alerts_are_queued(db_session, patient, event_payload, transport):
    from app.event_evaluations.service import evaluate_event
    from app.health_events.model import HealthEvent

    device(db_session, caregiver(db_session, patient), "synthetic")
    for offset in (-15, 0):
        event = HealthEvent(
            **HealthEventCreate(
                **{
                    **event_payload,
                    "external_event_id": str(uuid4()),
                    "numeric_value": "151",
                    "recorded_at": event_payload["recorded_at"]
                    + timedelta(seconds=offset),
                }
            ).model_dump()
        )
        db_session.add(event)
        db_session.flush()
        evaluate_event(db_session, event)
    alert = db_session.scalar(select(Alert))
    alert.status = AlertStatus.ACKNOWLEDGED
    db_session.commit()
    transport.assert_not_called()
