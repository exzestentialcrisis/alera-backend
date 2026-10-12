from app.ai_insights.facts import _alert_facts
from app.alerts.model import AlertStatus
from app.event_evaluations.model import EvaluationSeverity


def test_alert_facts_counts_severity_and_unresolved_status():
    facts = _alert_facts(
        [
            (EvaluationSeverity.WARNING, AlertStatus.ACTIVE),
            (EvaluationSeverity.WARNING, AlertStatus.RESOLVED),
            (EvaluationSeverity.CRITICAL, AlertStatus.ACKNOWLEDGED),
            (EvaluationSeverity.INFO, AlertStatus.FALSE_ALARM),
        ]
    )

    assert facts.total == 4
    assert facts.active == 2
    assert facts.warning == 2
    assert facts.critical == 1


def test_alert_facts_accepts_empty_period():
    facts = _alert_facts([])

    assert facts.total == 0
    assert facts.active == 0
    assert facts.warning == 0
    assert facts.critical == 0
