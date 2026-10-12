from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.notifications.sms import SemaphoreSMSSender


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("09171234567", "639171234567"),
        ("+63 917 123 4567", "639171234567"),
        ("639171234567", "639171234567"),
    ],
)
def test_normalizes_philippine_mobile_numbers(raw, expected):
    assert SemaphoreSMSSender.normalize_philippine_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "+1 555 123 4567"])
def test_rejects_non_philippine_mobile_numbers(raw):
    with pytest.raises(ValueError):
        SemaphoreSMSSender.normalize_philippine_number(raw)


def test_sender_is_disabled_without_explicit_configuration(monkeypatch):
    post = Mock()
    monkeypatch.setattr("app.notifications.sms.httpx.post", post)
    sender = SemaphoreSMSSender(Settings())
    sender.send("09171234567", "Critical alert")
    post.assert_not_called()


def test_sender_posts_without_exposing_secret(monkeypatch):
    response = Mock()
    response.raise_for_status = Mock()
    post = Mock(return_value=response)
    monkeypatch.setattr("app.notifications.sms.httpx.post", post)
    sender = SemaphoreSMSSender(
        Settings(sms_enabled=True, semaphore_api_key="test-secret")
    )

    sender.send("09171234567", "Critical alert")

    post.assert_called_once()
    assert post.call_args.kwargs["data"] == {
        "apikey": "test-secret",
        "number": "639171234567",
        "message": "Critical alert",
    }
    response.raise_for_status.assert_called_once_with()
