import pytest
from pydantic import ValidationError

from app.patients.schema import PatientCreate


@pytest.mark.parametrize("payload", [
    {}, {"full_name": " "}, {"full_name": "x" * 151},
    {"full_name": "Patient", "household_id": "arbitrary"},
    {"full_name": "Patient", "assigned_caregiver_id": "arbitrary"},
    {"full_name": "Patient", "baseline_heart_rate": 0},
    {"full_name": "Patient", "baseline_spo2": 101},
    {"full_name": "Patient", "baseline_spo2": "NaN"},
    {"full_name": "Patient", "sex": "UNKNOWN"},
    {"full_name": "Patient", "phone_number": "1" * 12},
])
def test_invalid_patient_input(payload):
    with pytest.raises(ValidationError):
        PatientCreate.model_validate(payload)


def test_optional_fields_and_normalized_name():
    patient = PatientCreate(full_name=" Patient ")
    assert patient.full_name == "Patient"
    assert patient.birthdate is None and patient.sex is None
