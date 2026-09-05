from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.notifications.fcm import SCOPE, FCMSender


def settings(**kwargs):
    return Settings(
        _env_file=None,
        fcm_enabled=True,
        firebase_project_id="alera-test",
        firebase_service_account_json="{}",
        **kwargs
    )


@pytest.mark.parametrize(
    "code,detail_type,removable",
    [
        ("UNREGISTERED", "type.googleapis.com/google.firebase.fcm.v1.FcmError", True),
        (
            "INVALID_ARGUMENT",
            "type.googleapis.com/google.firebase.fcm.v1.FcmError",
            True,
        ),
        ("INVALID_ARGUMENT", "type.googleapis.com/google.rpc.BadRequest", False),
        (
            "SENDER_ID_MISMATCH",
            "type.googleapis.com/google.firebase.fcm.v1.FcmError",
            False,
        ),
        ("UNAVAILABLE", "type.googleapis.com/google.firebase.fcm.v1.FcmError", False),
    ],
)
def test_error_classification(monkeypatch, code, detail_type, removable):
    sender = FCMSender(settings())
    monkeypatch.setattr(sender, "_access_token", lambda: "mock-oauth")
    post = Mock(
        return_value=httpx.Response(
            400,
            json={"error": {"details": [{"@type": detail_type, "errorCode": code}]}},
        )
    )
    monkeypatch.setattr("app.notifications.fcm.httpx.post", post)
    assert sender.send("synthetic", alert_id=uuid4(), patient_id=uuid4()) is removable
    assert post.call_args.args == (
        "https://fcm.googleapis.com/v1/projects/alera-test/messages:send",
    )
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer mock-oauth"}
    assert post.call_args.kwargs["timeout"] == 10


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text="unavailable"),
        httpx.Response(400, json={"error": {"details": None}}),
    ],
)
def test_malformed_response_safe(monkeypatch, response):
    sender = FCMSender(settings())
    monkeypatch.setattr(sender, "_access_token", lambda: "mock-oauth")
    monkeypatch.setattr("app.notifications.fcm.httpx.post", Mock(return_value=response))
    assert sender.send("synthetic", alert_id=uuid4(), patient_id=uuid4()) is False


def test_oauth_flow_mocked_without_credentials(monkeypatch):
    from google.auth.transport import requests
    from google.oauth2 import service_account

    credentials = Mock(valid=False, token="mock-oauth")
    factory = Mock(return_value=credentials)
    request = Mock()
    monkeypatch.setattr(
        service_account.Credentials, "from_service_account_info", factory
    )
    monkeypatch.setattr(requests, "Request", lambda: request)
    credentials.refresh.side_effect = lambda callback: callback(
        url="https://oauth2.googleapis.com/token", method="POST"
    )
    sender = FCMSender(settings())
    assert sender._access_token() == "mock-oauth"
    factory.assert_called_once_with(
        {"token_uri": "https://oauth2.googleapis.com/token"}, scopes=[SCOPE]
    )
    assert request.call_args.kwargs["timeout"] == 10
    request.session.close.assert_called_once()


def test_malformed_credentials_and_logs(monkeypatch, caplog):
    # Alembic migration tests disable existing loggers via fileConfig.
    from app.notifications.fcm import logger

    monkeypatch.setattr(logger, "disabled", False)
    configured = settings()
    from pydantic import SecretStr

    configured.firebase_service_account_json = SecretStr("not-json")
    sender = FCMSender(configured)
    post = Mock(side_effect=AssertionError("network must not be used"))
    monkeypatch.setattr("app.notifications.fcm.httpx.post", post)
    assert (
        sender.send("synthetic-private-token", alert_id=uuid4(), patient_id=uuid4())
        is False
    )
    post.assert_not_called()
    assert "synthetic-private-token" not in caplog.text
    assert "not-json" not in caplog.text
    assert "delivery unavailable" in caplog.text
