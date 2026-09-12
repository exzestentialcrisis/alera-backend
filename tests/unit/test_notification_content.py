from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.alerts.model import Alert
from app.alerts.service import alert_display_payload
from app.event_evaluations.model import ConditionKey, EvaluationSeverity
from app.notifications.content import DEFAULT_BODY, DEFAULT_TITLE, notification_content


def display(condition=ConditionKey.HR_HIGH, severity=EvaluationSeverity.CRITICAL,
            value=Decimal("154.00"), patient=True, nickname="Alera Test Patient",
            full_name="Full Name"):
    alert = Alert(alert_id=uuid4(), patient_id=uuid4(),
                  condition_key=condition, severity=severity)
    return alert_display_payload(
        alert,
        event=SimpleNamespace(numeric_value=value) if value is not None else None,
        patient=SimpleNamespace(nickname=nickname) if patient else None,
        user=SimpleNamespace(full_name=full_name) if patient else None,
    )


@pytest.mark.parametrize("condition,severity,value,title,body", [
    (ConditionKey.HR_HIGH, EvaluationSeverity.CRITICAL, "154.00",
     "Critical: High Heart Rate", "Alera Test Patient • 154 BPM"),
    (ConditionKey.SPO2_LOW, EvaluationSeverity.WARNING, "92.00",
     "Warning: Low SpO₂", "Alera Test Patient • 92%"),
    (ConditionKey.SPO2_LOW, EvaluationSeverity.CRITICAL, "88.00",
     "Critical: Low SpO₂", "Alera Test Patient • 88%"),
    (ConditionKey.HR_LOW, EvaluationSeverity.WARNING, "49.50",
     "Warning: Low Heart Rate", "Alera Test Patient • 49.5 BPM"),
])
def test_display_content(condition, severity, value, title, body):
    assert notification_content(display(condition, severity, Decimal(value))) == (title, body)


@pytest.mark.parametrize("overrides", [
    {"patient": False}, {"value": None}, {"nickname": None, "full_name": None},
])
def test_missing_patient_or_reading_uses_body_fallback(overrides):
    assert notification_content(display(**overrides)) == (
        "Critical: High Heart Rate", DEFAULT_BODY,
    )


def test_name_falls_back_to_full_name_like_api():
    assert notification_content(display(nickname=None))[1] == "Full Name • 154 BPM"


@pytest.mark.parametrize("field", ["title", "severity"])
def test_missing_title_field_uses_title_fallback(field):
    data = display()
    data[field] = None
    assert notification_content(data) == (DEFAULT_TITLE, "Alera Test Patient • 154 BPM")


def test_missing_unit_and_unknown_mapping_fallbacks():
    data = display()
    data["reading_unit"] = None
    assert notification_content(data)[1] == DEFAULT_BODY
    assert notification_content(display(condition=None)) == (DEFAULT_TITLE, DEFAULT_BODY)
