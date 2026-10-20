from types import SimpleNamespace

import pytest

from app.ai_insights.provider import OpenAIInsightRankingProvider


class FakeResponses:
    def __init__(self, output_text):
        self.output_text = output_text
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, output_text):
        self.responses_api = FakeResponses(output_text)

    @property
    def responses(self):
        return self.responses_api


def test_openai_provider_requests_strict_structured_output_without_storage():
    client = FakeClient('{"selected_ids":["SAFE_CARD"]}')
    provider = OpenAIInsightRankingProvider(
        api_key="test-key",
        model="gpt-5.4-mini",
        client=client,
    )

    result = provider.rank("Rank these already-approved cards.")

    assert result == '{"selected_ids":["SAFE_CARD"]}'
    assert client.responses_api.request["model"] == "gpt-5.4-mini"
    assert client.responses_api.request["store"] is False
    assert client.responses_api.request["text"]["format"]["type"] == "json_schema"
    assert client.responses_api.request["text"]["format"]["strict"] is True


def test_openai_provider_rejects_empty_output():
    provider = OpenAIInsightRankingProvider(
        api_key="test-key",
        model="gpt-5.4-mini",
        client=FakeClient(""),
    )

    with pytest.raises(RuntimeError, match="returned no insight ranking"):
        provider.rank("Rank these already-approved cards.")
