from types import SimpleNamespace

import pytest

from app.ai_insights.provider import GeminiInsightRankingProvider


class FakeInteractions:
    def __init__(self, output_text):
        self.output_text = output_text
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, output_text):
        self.interactions_api = FakeInteractions(output_text)

    @property
    def interactions(self):
        return self.interactions_api


def test_gemini_provider_uses_stateless_structured_output():
    client = FakeClient('{"selected_ids":["SAFE_CARD"]}')
    provider = GeminiInsightRankingProvider(
        api_key="test-key",
        model="gemini-3.8-flash",
        client=client,
    )

    result = provider.rank("Rank these already-approved cards.")

    assert result == '{"selected_ids":["SAFE_CARD"]}'
    assert client.interactions_api.request["model"] == "gemini-3.8-flash"
    assert client.interactions_api.request["store"] is False
    response_format = client.interactions_api.request["response_format"]
    assert response_format["mime_type"] == "application/json"
    assert response_format["schema"]["required"] == ["selected_ids"]


def test_gemini_provider_rejects_empty_output():
    provider = GeminiInsightRankingProvider(
        api_key="test-key",
        model="gemini-3.8-flash",
        client=FakeClient(""),
    )

    with pytest.raises(RuntimeError, match="returned no insight ranking"):
        provider.rank("Rank these already-approved cards.")
