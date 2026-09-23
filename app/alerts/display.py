from dataclasses import dataclass

from app.event_evaluations.model import ConditionKey
from app.health_events.model import MetricType


@dataclass(frozen=True)
class AlertDisplayMapping:
    title: str
    metric_type: MetricType
    unit: str


ALERT_DISPLAY_MAPPINGS: dict[ConditionKey, AlertDisplayMapping] = {
    ConditionKey.HR_HIGH: AlertDisplayMapping(
        "High Heart Rate",
        MetricType.HEART_RATE,
        "BPM",
    ),
    ConditionKey.HR_LOW: AlertDisplayMapping(
        "Low Heart Rate",
        MetricType.HEART_RATE,
        "BPM",
    ),
    ConditionKey.SPO2_LOW: AlertDisplayMapping("Low SpO₂", MetricType.SPO2, "%"),

    ConditionKey.PHONE_DISCONNECTED: AlertDisplayMapping(
    "Patient Phone Disconnected",
    MetricType.CONNECTION_STATUS,
    "",
    ),
    ConditionKey.WATCH_DISCONNECTED: AlertDisplayMapping(
    "Smartwatch Disconnected",
    MetricType.CONNECTION_STATUS,
    "",
    ),
    ConditionKey.PHONE_BATTERY_LOW: AlertDisplayMapping(
    "Patient Phone Battery Low",
    MetricType.BATTERY_LEVEL,
    "%",
    ),
    ConditionKey.WATCH_BATTERY_LOW: AlertDisplayMapping(
    "Smartwatch Battery Low",
    MetricType.BATTERY_LEVEL,
    "%",
    ),
    ConditionKey.INACTIVITY: AlertDisplayMapping(
    "No Movement Detected",
    MetricType.INACTIVITY,
    "hr",
    ),
    ConditionKey.WATCH_NOT_WORN: AlertDisplayMapping(
    "Smartwatch Not Worn",
    MetricType.CONNECTION_STATUS,
    "",
    ),
    ConditionKey.PATIENT_LOGGED_OUT: AlertDisplayMapping(
    "Patient Logged Out",
    MetricType.CONNECTION_STATUS,
    "",
    ),
}

# Future condition support belongs here. In particular, activity conditions can
# map to MetricType.ACTIVITY / "Low Activity", while device conditions can map
# to a DEVICE_STATUS metric once that metric is part of the domain model.


def display_mapping(condition_key: ConditionKey) -> AlertDisplayMapping | None:
    return ALERT_DISPLAY_MAPPINGS.get(condition_key)
