from pydantic import ValidationError

from app.ai_insights.candidates import build_insight_candidates
from app.ai_insights.prompt import build_ranking_prompt
from app.ai_insights.provider import InsightRankingProvider
from app.ai_insights.schema import (
    CaregiverInsightResponse,
    InsightCandidate,
    InsightFactPacket,
    InsightPriority,
    InsightSelection,
    InsightSource,
)

DISCLAIMER = (
    "These insights summarize recorded Alera data and are not a diagnosis or "
    "a replacement for professional medical advice."
)


def _summary(items: list[InsightCandidate]) -> str:
    priorities = {item.priority for item in items}
    if InsightPriority.CRITICAL in priorities:
        return "Critical findings need prompt caregiver review."
    if InsightPriority.WARNING in priorities:
        return "Some findings need caregiver attention."
    if InsightPriority.INFORMATIONAL in priorities:
        return "Available monitoring data has coverage gaps."
    return "No priority findings were identified in the available data."


def _response(
    packet: InsightFactPacket,
    items: list[InsightCandidate],
    source: InsightSource,
) -> CaregiverInsightResponse:
    return CaregiverInsightResponse(
        patient_id=packet.patient_id,
        from_at=packet.from_at,
        to_at=packet.to_at,
        source=source,
        summary=_summary(items),
        items=items,
        disclaimer=DISCLAIMER,
    )


def generate_caregiver_insights(
    packet: InsightFactPacket,
    provider: InsightRankingProvider | None = None,
) -> CaregiverInsightResponse:
    """Return deterministic statements, optionally ordered by an AI provider."""
    candidates = build_insight_candidates(packet)
    fallback_items = candidates[:3]
    if provider is None:
        return _response(packet, fallback_items, InsightSource.FALLBACK)

    try:
        raw_selection = provider.rank(build_ranking_prompt(candidates))
        selection = InsightSelection.model_validate_json(raw_selection)
        by_id = {candidate.insight_id: candidate for candidate in candidates}
        if len(set(selection.selected_ids)) != len(selection.selected_ids):
            raise ValueError("AI selection contains duplicate IDs.")
        selected = [by_id[insight_id] for insight_id in selection.selected_ids]
    except (KeyError, TypeError, ValueError, ValidationError, RuntimeError):
        return _response(packet, fallback_items, InsightSource.FALLBACK)

    return _response(packet, selected, InsightSource.AI_RANKED)
