"""Development-only remote push demo; no database or Firebase credentials needed."""

import argparse
from datetime import datetime, timezone
import os
import sys
import time
from uuid import UUID, uuid4

import httpx

# Deliberately fixed to the explicit seed_demo_data fixture; no target overrides.
DEMO_PATIENT_ID = "a076ecdb-ae38-4f84-b490-e714977027ee"
DEMO_HOUSEHOLD_ID = "33333333-3333-3333-3333-333333333333"
DEMO_HOUSEHOLD_CODE = "4V8F-29HC"
DEMO_PATIENT_NAME = "Alera Test Patient"
POLL_ATTEMPTS = 10
POLL_INTERVAL = 2


class DemoError(Exception):
    """A safe, credential-free operational error."""


def request(client, method, path, stage, **kwargs):
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError:
        raise DemoError(f"{stage}: network failure or timeout; check Render logs.") from None
    if not response.is_success:
        raise DemoError(f"{stage}: HTTP {response.status_code}; check deployment and access.")
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except ValueError:
        raise DemoError(f"{stage}: invalid API response.") from None


def checked_alert(data, status):
    if (
        not isinstance(data, dict)
        or data.get("patient_id") != DEMO_PATIENT_ID
        or data.get("condition_key") != "HR_HIGH"
        or data.get("status") != status
    ):
        raise DemoError("Alert response does not match the required demo fixture/state.")
    try:
        return str(UUID(data["alert_id"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise DemoError("Alert response has no valid alert ID.") from None


def active_alerts(client):
    data = request(client, "GET", "/api/v1/alerts", "Demo alert lookup", params={
        "patient_id": DEMO_PATIENT_ID, "condition_key": "HR_HIGH",
        "status": "ACTIVE", "limit": 100, "offset": 0,
    })
    items = data.get("items")
    if not isinstance(items, list) or data.get("total") != len(items):
        raise DemoError("Demo alert lookup is incomplete or malformed.")
    for item in items:
        checked_alert(item, "ACTIVE")
        if item.get("patient_display_name") != DEMO_PATIENT_NAME:
            raise DemoError("Demo fixture patient display name does not match.")
    return items


def trigger(client, email, password, *, confirm_demo=False):
    if not confirm_demo:
        raise DemoError("Development demo requires --confirm-demo before any API calls.")
    login = request(client, "POST", "/api/v1/auth/caregiver/login", "Caregiver authentication", json={
        "household_code": DEMO_HOUSEHOLD_CODE, "email": email, "password": password,
    })
    actor = login.get("actor")
    if (
        not isinstance(actor, dict)
        or actor.get("household_id") != DEMO_HOUSEHOLD_ID
        or actor.get("household_code") != DEMO_HOUSEHOLD_CODE
        or actor.get("role") not in ("CAREGIVER", "CARE_ADMIN")
    ):
        raise DemoError("Authenticated actor does not belong to the demo household.")
    token = login.get("access_token")
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise DemoError("Caregiver authentication returned an invalid access token.")
    client.headers["Authorization"] = f"Bearer {token}"
    current = active_alerts(client)
    if len(current) != 1:
        raise DemoError("Expected exactly one ACTIVE demo HR_HIGH alert; check fixture setup/state.")
    old_id = checked_alert(current[0], "ACTIVE")
    resolved = request(client, "POST", f"/api/v1/alerts/{old_id}/resolve", "Demo alert resolution", json={
        "note": "Development push demo: resolve fixture alert before a fresh reading.",
    })
    if checked_alert(resolved.get("alert"), "RESOLVED") != old_id:
        raise DemoError("Resolution returned a different alert ID.")

    external_id = f"alera-push-demo-{uuid4()}"
    event = request(client, "POST", "/api/v1/health-events", "Health-event ingestion", json={
        "patient_id": DEMO_PATIENT_ID, "external_event_id": external_id,
        "metric_type": "HEART_RATE", "numeric_value": "154", "metric_unit": "BPM",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "validation_status": "VALID_REALTIME",
        "raw_payload": {"demo": True, "source": "trigger_push_demo.py"},
    })
    if event.get("patient_id") != DEMO_PATIENT_ID or event.get("external_event_id") != external_id:
        raise DemoError("Ingestion response does not match the submitted demo event.")
    for attempt in range(POLL_ATTEMPTS):
        for alert in active_alerts(client):
            new_id = checked_alert(alert, "ACTIVE")
            if new_id == old_id:
                continue
            detail = request(client, "GET", f"/api/v1/alerts/{new_id}", "New alert verification")
            triggering = detail.get("triggering_event")
            if (checked_alert(detail, "ACTIVE") == new_id
                    and isinstance(triggering, dict)
                    and triggering.get("external_event_id") == external_id):
                return new_id
        if attempt + 1 < POLL_ATTEMPTS:
            time.sleep(POLL_INTERVAL)
    raise DemoError(
        "No new ACTIVE HR_HIGH alert linked to this event appeared. Check Render logs, "
        "fixture state and future-dated seed readings; ingestion may already have committed."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-demo", action="store_true", help="Allow fixture alert resolution and event ingestion")
    args = parser.parse_args(argv)
    try:
        if not args.confirm_demo:
            raise DemoError("Development demo requires --confirm-demo before any API calls.")
        values = {}
        for name in ("ALERA_DEMO_BASE_URL", "ALERA_DEMO_CAREGIVER_EMAIL", "ALERA_DEMO_CAREGIVER_PASSWORD"):
            values[name] = os.environ.get(name, "")
            if not values[name].strip():
                raise DemoError(f"Required environment variable {name} is missing.")
        try:
            url = httpx.URL(values["ALERA_DEMO_BASE_URL"])
        except httpx.InvalidURL:
            raise DemoError("ALERA_DEMO_BASE_URL must be a valid HTTPS origin.") from None
        if (url.scheme != "https" or not url.host or url.userinfo or url.query
                or url.fragment or url.path not in ("", "/")):
            raise DemoError("ALERA_DEMO_BASE_URL must be an HTTPS origin without credentials, path, query or fragment.")
        with httpx.Client(base_url=url, timeout=60, follow_redirects=False) as client:
            alert_id = trigger(client, values["ALERA_DEMO_CAREGIVER_EMAIL"], values["ALERA_DEMO_CAREGIVER_PASSWORD"], confirm_demo=True)
        print(f"New alert ID: {alert_id}\nStatus: ACTIVE\nCondition key: HR_HIGH")
        print("Check the registered caregiver phone and Render logs for push delivery.")
        return 0
    except DemoError as exc:
        print(f"Push demo failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
