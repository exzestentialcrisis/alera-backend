import pytest
from pydantic import ValidationError

from app.alerts.schema import (
    FalseAlarmRequest,
    InterventionRequest,
    InterventionType,
    NoteRequest,
    OptionalNoteRequest,
)


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (FalseAlarmRequest, {"reason": "   "}),
        (NoteRequest, {"note": ""}),
        (
            InterventionRequest,
            {"intervention_type": "PATIENT_CHECK", "note": "  "},
        ),
        (OptionalNoteRequest, {"note": "\t"}),
    ],
)
def test_alert_action_text_rejects_blank_values(schema, payload):
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_intervention_type_is_validated_and_note_is_trimmed():
    request = InterventionRequest.model_validate(
        {
            "intervention_type": "PATIENT_CHECK",
            "note": "  Patient was checked. ",
        }
    )

    assert request.intervention_type is InterventionType.PATIENT_CHECK
    assert request.note == "Patient was checked."


def test_invalid_intervention_type_is_rejected():
    with pytest.raises(ValidationError):
        InterventionRequest.model_validate(
            {"intervention_type": "UNKNOWN_ACTION", "note": "Checked"}
        )


def test_actor_id_is_not_accepted_in_action_body():
    with pytest.raises(ValidationError):
        OptionalNoteRequest.model_validate(
            {"note": "Reviewing", "actor_id": "00000000-0000-0000-0000-000000000000"}
        )
