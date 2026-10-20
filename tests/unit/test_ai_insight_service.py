from datetime import datetime, timezone
from uuid import uuid4

from app.ai_insights.candidates import build_insight_candidates
from app.ai_insights.prompt import build_ranking_prompt
from app.ai_insights.schema import (
    ActivityFacts,
    AlertFacts,
    InsightFactPacket,
    InsightSource,
    SleepFacts,
    VitalFacts,
)
from app.ai_insights.service import generate_caregiver_insights


class FakeProvider:
    def __init__(self, response: str | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.prompt = None

    def rank(self, prompt: str) -> str:
        self.prompt = prompt
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def _packet(
    *,
    critical: int = 0,
    warning: int = 0,
    active: int = 0,
    heart_rate_latest: float | None = 75,
    spo2_latest: float | None = 97,
    missing_data: list[str] | None = None,
) -> InsightFactPacket:
    return InsightFactPacket(
        patient_id=uuid4(),
        from_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
        to_at=datetime(2026, 10, 20, tzinfo=timezone.utc),
        heart_rate=VitalFacts(
            unit="bpm",
            latest=heart_rate_latest,
            reading_count=10 if heart_rate_latest is not None else 0,
            normal_min=60,
            normal_max=100,
        ),
        spo2=VitalFacts(
            unit="%",
            latest=spo2_latest,
            reading_count=10 if spo2_latest is not None else 0,
            normal_min=94,
            normal_max=100,
        ),
        activity=ActivityFacts(days_with_data=7),
        sleep=SleepFacts(nights_with_data=7),
        alerts=AlertFacts(
            total=critical + warning,
            active=active,
            warning=warning,
            critical=critical,
        ),
        missing_data=missing_data or [],
    )


def test_candidates_are_deterministic_and_fact_grounded():
    candidates = build_insight_candidates(
        _packet(
            critical=2,
            active=1,
            spo2_latest=91,
            missing_data=["sleep"],
        )
    )

    assert candidates[0].insight_id == "CRITICAL_ALERTS_RECORDED"
    assert "2 critical alert(s)" in candidates[0].message
    assert {candidate.insight_id for candidate in candidates} >= {
        "UNRESOLVED_ALERTS_PRESENT",
        "SPO2_LATEST_BELOW_RANGE",
        "MISSING_MONITORING_DATA",
    }


def test_valid_provider_can_only_rank_approved_candidates():
    provider = FakeProvider(
        '{"selected_ids":["SPO2_LATEST_BELOW_RANGE","CRITICAL_ALERTS_RECORDED"]}'
    )

    response = generate_caregiver_insights(
        _packet(critical=1, spo2_latest=91),
        provider,
    )

    assert response.source is InsightSource.AI_RANKED
    # Critical insights are mandatory and always appear first.
    assert [item.insight_id for item in response.items] == [
        "CRITICAL_ALERTS_RECORDED",
        "SPO2_LATEST_BELOW_RANGE",
    ]
    assert provider.prompt is not None


def test_unknown_provider_id_uses_safe_fallback():
    response = generate_caregiver_insights(
        _packet(critical=1),
        FakeProvider('{"selected_ids":["INVENTED_DIAGNOSIS"]}'),
    )

    assert response.source is InsightSource.FALLBACK
    assert response.items[0].insight_id == "CRITICAL_ALERTS_RECORDED"


def test_provider_failure_uses_safe_fallback():
    response = generate_caregiver_insights(
        _packet(warning=1),
        FakeProvider(error=RuntimeError("provider unavailable")),
    )

    assert response.source is InsightSource.FALLBACK
    assert response.items[0].insight_id == "WARNING_ALERTS_RECORDED"


def test_prompt_excludes_patient_identifier_and_forbids_new_claims():
    packet = _packet(critical=1)
    prompt = build_ranking_prompt(build_insight_candidates(packet))

    assert str(packet.patient_id) not in prompt
    assert "do not add medical advice" in prompt
    assert "CRITICAL_ALERTS_RECORDED" in prompt


def test_ai_cannot_omit_critical_alert_insight():
    response = generate_caregiver_insights(
        _packet(critical=1, warning=1),
        FakeProvider('{"selected_ids":["WARNING_ALERTS_RECORDED"]}'),
    )

    assert response.source is InsightSource.AI_RANKED
    assert [item.insight_id for item in response.items] == [
        "CRITICAL_ALERTS_RECORDED",
        "WARNING_ALERTS_RECORDED",
    ]
