import json

from app.ai_insights.schema import InsightCandidate


def build_ranking_prompt(candidates: list[InsightCandidate]) -> str:
    approved = [
        {
            "insight_id": candidate.insight_id,
            "priority": candidate.priority.value,
            "title": candidate.title,
            "message": candidate.message,
        }
        for candidate in candidates
    ]
    return (
        "You rank caregiver insights that were already verified by Alera's "
        "deterministic health rules. Select one to three insight_id values. "
        "Prioritize urgent and actionable items, preserve the supplied IDs "
        "exactly, and do not add medical advice, diagnoses, measurements, or "
        "new text. Return JSON only in this exact shape: "
        '{"selected_ids":["ID"]}. Approved candidates: '
        + json.dumps(approved, ensure_ascii=False, separators=(",", ":"))
    )
