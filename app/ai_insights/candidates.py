from app.ai_insights.schema import (
    InsightCandidate,
    InsightFactPacket,
    InsightPriority,
)

PRIORITY_ORDER = {
    InsightPriority.CRITICAL: 0,
    InsightPriority.WARNING: 1,
    InsightPriority.INFORMATIONAL: 2,
    InsightPriority.POSITIVE: 3,
}


def _outside_latest_candidates(
    packet: InsightFactPacket,
) -> list[InsightCandidate]:
    candidates = []
    for label, prefix, facts in (
        ("Heart rate", "HEART_RATE", packet.heart_rate),
        ("SpO₂", "SPO2", packet.spo2),
    ):
        if facts.latest is None:
            continue
        if facts.normal_min is not None and facts.latest < facts.normal_min:
            candidates.append(
                InsightCandidate(
                    insight_id=f"{prefix}_LATEST_BELOW_RANGE",
                    priority=InsightPriority.WARNING,
                    title=f"Latest {label.lower()} is below the configured range",
                    message=(
                        f"The latest {label} reading was {facts.latest:g} "
                        f"{facts.unit}; the configured lower threshold is "
                        f"{facts.normal_min:g} {facts.unit}."
                    ),
                    evidence_keys=[
                        f"{prefix.lower()}.latest",
                        f"{prefix.lower()}.normal_min",
                    ],
                )
            )
        elif facts.normal_max is not None and facts.latest > facts.normal_max:
            candidates.append(
                InsightCandidate(
                    insight_id=f"{prefix}_LATEST_ABOVE_RANGE",
                    priority=InsightPriority.WARNING,
                    title=f"Latest {label.lower()} is above the configured range",
                    message=(
                        f"The latest {label} reading was {facts.latest:g} "
                        f"{facts.unit}; the configured upper threshold is "
                        f"{facts.normal_max:g} {facts.unit}."
                    ),
                    evidence_keys=[
                        f"{prefix.lower()}.latest",
                        f"{prefix.lower()}.normal_max",
                    ],
                )
            )
    return candidates


def build_insight_candidates(
    packet: InsightFactPacket,
) -> list[InsightCandidate]:
    """Create deterministic, fact-grounded statements for optional AI ranking."""
    candidates = []

    if packet.alerts.critical:
        candidates.append(
            InsightCandidate(
                insight_id="CRITICAL_ALERTS_RECORDED",
                priority=InsightPriority.CRITICAL,
                title="Critical alerts were recorded",
                message=(
                    f"{packet.alerts.critical} critical alert(s) were recorded "
                    "during this seven-day period. Review the alert history."
                ),
                evidence_keys=["alerts.critical"],
            )
        )

    if packet.alerts.warning:
        candidates.append(
            InsightCandidate(
                insight_id="WARNING_ALERTS_RECORDED",
                priority=InsightPriority.WARNING,
                title="Warning alerts were recorded",
                message=(
                    f"{packet.alerts.warning} warning alert(s) were recorded "
                    "during this seven-day period. Review the alert history."
                ),
                evidence_keys=["alerts.warning"],
            )
        )

    if packet.alerts.active:
        candidates.append(
            InsightCandidate(
                insight_id="UNRESOLVED_ALERTS_PRESENT",
                priority=InsightPriority.WARNING,
                title="Unresolved alerts need review",
                message=(
                    f"{packet.alerts.active} alert(s) remain active or "
                    "acknowledged and unresolved."
                ),
                evidence_keys=["alerts.active"],
            )
        )

    candidates.extend(_outside_latest_candidates(packet))

    if packet.missing_data:
        labels = {
            "heart_rate": "heart rate",
            "spo2": "SpO₂",
            "activity": "activity",
            "sleep": "sleep",
        }
        missing = ", ".join(labels[item] for item in packet.missing_data)
        candidates.append(
            InsightCandidate(
                insight_id="MISSING_MONITORING_DATA",
                priority=InsightPriority.INFORMATIONAL,
                title="Some monitoring data is unavailable",
                message=(
                    f"No seven-day data was available for: {missing}. "
                    "Insights may be incomplete."
                ),
                evidence_keys=[f"missing_data.{item}" for item in packet.missing_data],
            )
        )

    if not candidates:
        candidates.append(
            InsightCandidate(
                insight_id="NO_PRIORITY_FINDINGS",
                priority=InsightPriority.POSITIVE,
                title="No priority findings identified",
                message=(
                    "No alerts or latest vital readings outside configured "
                    "thresholds were identified in the available seven-day data."
                ),
                evidence_keys=[
                    "alerts.critical",
                    "alerts.warning",
                    "alerts.active",
                    "heart_rate.latest",
                    "spo2.latest",
                ],
            )
        )

    return sorted(
        candidates,
        key=lambda candidate: (
            PRIORITY_ORDER[candidate.priority],
            candidate.insight_id,
        ),
    )
