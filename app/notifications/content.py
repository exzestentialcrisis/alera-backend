"""Format only the explicitly allowed alert display fields for notification text."""

from decimal import Decimal, InvalidOperation

DEFAULT_TITLE = "Alera health alert"
DEFAULT_BODY = "A new alert needs your attention."


def notification_content(display: dict) -> tuple[str, str]:
    title = DEFAULT_TITLE
    body = DEFAULT_BODY
    severity = display.get("severity")
    alert_title = display.get("title")
    if severity and alert_title:
        title = f"{severity.title()}: {alert_title}"

    name = display.get("patient_display_name")
    condition_key = display.get("condition_key")
    condition_value = getattr(
        condition_key,
        "value",
        condition_key,
    )

    if condition_value == "WATCH_NOT_WORN":
        if name:
            body = (
                f"{name}'s smartwatch has been off wrist "
                "for at least 3 minutes."
            )
        else:
            body = (
                "The patient's smartwatch has been off wrist "
                "for at least 3 minutes."
            )

        return title, body


    value = display.get("reading_value")
    unit = display.get("reading_unit")
    if name and value is not None and unit:
        try:
            number = Decimal(str(value))
            if number.is_finite():
                reading = format(number, "f")
                if "." in reading:
                    reading = reading.rstrip("0").rstrip(".")
                separator = "" if unit == "%" else " "
                body = f"{name} • {reading}{separator}{unit}"
        except InvalidOperation:
            pass
    return title, body
