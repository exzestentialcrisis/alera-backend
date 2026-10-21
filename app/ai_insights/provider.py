from typing import Any, Protocol


class InsightRankingProvider(Protocol):
    """AI ranks approved statements; it does not invent caregiver insights."""

    def rank(self, prompt: str) -> str:
        """Return JSON matching InsightSelection."""
        ...


class GeminiInsightRankingProvider:
    """Rank approved insight candidates through Google's Gemini Interactions API."""

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
            from google import genai

            self._client = genai.Client(api_key=self._api_key)

        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": {
                "type": "object",
                "properties": {
                    "selected_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["selected_ids"],
                "additionalProperties": False,
            },
        }

        try:
            response = self._client.interactions.create(
                model=self._model,
                input=prompt,
                store=False,
                response_format=response_format,
            )
        except Exception as exc:
            raise RuntimeError("Gemini insight ranking request failed.") from exc

        output = response.output_text
        if not output:
            raise RuntimeError("Gemini returned no insight ranking.")

        return output
