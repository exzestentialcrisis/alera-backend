from typing import Protocol


class InsightRankingProvider(Protocol):
    """Provider boundary: AI may rank approved statements, not invent them."""

    def rank(self, prompt: str) -> str:
        """Return JSON matching InsightSelection."""
        ...
