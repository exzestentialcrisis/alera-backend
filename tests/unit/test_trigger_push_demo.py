import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from scripts import trigger_push_demo as demo
from app.alerts.schema import OptionalNoteRequest
from app.health_events.schema import HealthEventCreate


class API:
    def __init__(self):
        self.old = str(uuid4())
        self.new = str(uuid4())
        self.event = None
        self.calls = []
        self.fail = None
        self.wrong_household = False
        self.missing = False
        self.wrong_patient = False
        self.no_new = False
        self.wrong_event = False
        self.polls = 0

    def alert(self, alert_id, status="ACTIVE"):
        return {"alert_id": alert_id, "status": status,
                "patient_id": str(uuid4()) if self.wrong_patient else demo.DEMO_PATIENT_ID,
                "condition_key": "HR_HIGH", "patient_display_name": demo.DEMO_PATIENT_NAME}

    def handle(self, req):
        path = req.url.path
        self.calls.append(req)
        if path == self.fail:
            return httpx.Response(403, text="password bearer fcm service-account SECRET")
        if path.endswith("/login"):
            assert json.loads(req.content) == {
                "email": "demo@example.com", "password": "SECRET",
                "household_code": demo.DEMO_HOUSEHOLD_CODE,
            }
            return httpx.Response(200, json={"access_token": "SECRET",
                "actor": {"household_id": "wrong" if self.wrong_household else demo.DEMO_HOUSEHOLD_ID,
                          "household_code": demo.DEMO_HOUSEHOLD_CODE, "role": "CAREGIVER"}})
        assert req.headers["Authorization"] == "Bearer SECRET"
        if path == "/api/v1/alerts":
            assert req.url.params["patient_id"] == demo.DEMO_PATIENT_ID
            assert req.url.params["status"] == "ACTIVE"
            assert req.url.params["condition_key"] == "HR_HIGH"
            self.polls += 1
            items = [] if self.missing else [self.alert(self.old if self.event is None or self.no_new or self.polls == 2 else self.new)]
            return httpx.Response(200, json={"items": items, "total": len(items)})
        if path.endswith("/resolve"):
            OptionalNoteRequest.model_validate(json.loads(req.content))
            assert path == f"/api/v1/alerts/{self.old}/resolve"
            return httpx.Response(200, json={"alert": self.alert(self.old, "RESOLVED")})
        if path == "/api/v1/health-events":
            self.event = json.loads(req.content)
            HealthEventCreate.model_validate(self.event)
            assert self.event["numeric_value"] == "154"
            assert self.event["metric_type"] == "HEART_RATE"
            assert self.event["validation_status"] == "VALID_REALTIME"
            recorded = datetime.fromisoformat(self.event["recorded_at"])
            assert abs((datetime.now(timezone.utc) - recorded).total_seconds()) < 5
            return httpx.Response(200, json=self.event)
        assert path == f"/api/v1/alerts/{self.new}"
        return httpx.Response(200, json={**self.alert(self.new), "triggering_event": {
            "external_event_id": "unrelated" if self.wrong_event else self.event["external_event_id"]}})


def run(api, confirm=True):
    with httpx.Client(base_url="https://demo.example", transport=httpx.MockTransport(api.handle)) as client:
        return demo.trigger(client, "demo@example.com", "SECRET", confirm_demo=confirm)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(demo.time, "sleep", lambda _: None)


def test_success_repeat_uses_fresh_event_and_real_contract():
    api = API()
    assert run(api) == api.new
    first = api.event["external_event_id"]
    api.old = api.new
    api.new = str(uuid4())
    api.event = None
    assert run(api) == api.new
    assert api.event["external_event_id"] != first
    assert first.startswith("alera-push-demo-")


def test_confirmation_prevents_all_requests():
    api = API()
    with pytest.raises(demo.DemoError, match="--confirm-demo"):
        run(api, False)
    assert not api.calls


@pytest.mark.parametrize("flag", ["wrong_household", "missing", "wrong_patient"])
def test_fixture_guards_before_mutation(flag):
    api = API()
    setattr(api, flag, True)
    with pytest.raises(demo.DemoError):
        run(api)
    assert all(req.method == "GET" or req.url.path.endswith("/login") for req in api.calls)


@pytest.mark.parametrize("path", ["/api/v1/auth/caregiver/login", "/api/v1/alerts", "resolve", "/api/v1/health-events"])
def test_http_failures_are_safe(path):
    api = API()
    api.fail = f"/api/v1/alerts/{api.old}/resolve" if path == "resolve" else path
    with pytest.raises(demo.DemoError, match="HTTP 403") as exc:
        run(api)
    assert "SECRET" not in str(exc.value)
    assert api.calls[-1].url.path == api.fail


@pytest.mark.parametrize("flag", ["no_new", "wrong_event"])
def test_poll_timeout_does_not_claim_success(flag):
    api = API()
    setattr(api, flag, True)
    with pytest.raises(demo.DemoError, match="No new ACTIVE"):
        run(api)
    assert api.polls == demo.POLL_ATTEMPTS + 1


@pytest.mark.parametrize("failure", ["network", "json"])
def test_transport_and_json_errors_are_safe(failure):
    def handle(req):
        if failure == "network":
            raise httpx.ReadTimeout("SECRET", request=req)
        return httpx.Response(200, text="SECRET")
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="https://demo.example") as client:
        with pytest.raises(demo.DemoError) as exc:
            demo.trigger(client, "demo@example.com", "SECRET", confirm_demo=True)
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("url", ["http://demo.example", "https://user:SECRET@demo.example", "https://demo.example/?SECRET", "https://demo.example/api"])
def test_cli_rejects_unsafe_url(monkeypatch, capsys, url):
    monkeypatch.setenv("ALERA_DEMO_BASE_URL", url)
    monkeypatch.setenv("ALERA_DEMO_CAREGIVER_EMAIL", "demo@example.com")
    monkeypatch.setenv("ALERA_DEMO_CAREGIVER_PASSWORD", "SECRET")
    assert demo.main(["--confirm-demo"]) == 1
    assert "SECRET" not in capsys.readouterr().err


def test_cli_missing_config_and_confirmation(monkeypatch, capsys):
    monkeypatch.delenv("ALERA_DEMO_BASE_URL", raising=False)
    assert demo.main([]) == 1
    assert "--confirm-demo" in capsys.readouterr().err
    assert demo.main(["--confirm-demo"]) == 1
    assert "ALERA_DEMO_BASE_URL" in capsys.readouterr().err
