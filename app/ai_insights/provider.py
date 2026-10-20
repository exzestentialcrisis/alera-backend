from typing import Any, Protocol


class InsightRankingProvider(Protocol):
    """Provider boundary: AI may rank approved statements, not invent them."""

    def rank(self, prompt: str) -> str:
        """Return JSON matching InsightSelection."""
        ...


class OpenAIInsightRankingProvider:
    """Use OpenAI only to rank the already-approved insight candidates."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    def rank(self, prompt: str) -> str:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self._api_key,
                timeout=10.0,
                max_retries=0,
            )

        schema = {
            "type": "object",
            "properties": {
                "selected_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["selected_ids"],
            "additionalProperties": False,
        }

        try:
            response = self._client.responses.create(
                model=self._model,
                input=prompt,
                store=False,
                max_output_tokens=100,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "insight_selection",
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
        except Exception as exc:
            raise RuntimeError("OpenAI insight ranking request failed.") from exc

        output = response.output_text
        if not output:
            raise RuntimeError("OpenAI returned no insight ranking.")

        return output
