from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.reminders.enums import (
    ReminderCategory,
    ReminderOccurrenceStatus,
    ReminderPriority,
)
from app.reminders.access import resolve_list_patient_id, visible_patient_ids
from app.reminders.errors import (
    ReminderAccessForbiddenError,
    ReminderNotFoundError,
    ReminderQueryValidationError,
)
from app.reminders.errors import ReminderQueryValidationError
from app.reminders.model import ReminderOccurrence, ReminderTemplate
from app.reminders.service import reminder_occurrence_payload, validate_reminder_time_range
from app.reminders.schema import (
    ReminderCancelRequest,
    ReminderCaregiverCompleteRequest,
    ReminderCompleteRequest,
    ReminderSnoozeRequest,
)
from app.users.model import User, UserRole


class ScalarDatabase:
    def __init__(self, *values):
        self.values = iter(values)

    def scalar(self, _statement):
        return next(self.values)


def test_reminder_time_range_requires_aware_datetimes_and_valid_order():
    aware = datetime(2026, 9, 11, tzinfo=timezone.utc)

    with pytest.raises(ReminderQueryValidationError, match="from_at"):
        validate_reminder_time_range(datetime(2026, 9, 11), None)
    with pytest.raises(ReminderQueryValidationError, match="before_at"):
        validate_reminder_time_range(None, datetime(2026, 9, 12))
    with pytest.raises(ReminderQueryValidationError, match="before_at"):
        validate_reminder_time_range(aware, aware)


def test_reminder_patient_scope_rejects_a_different_requested_patient():
    actor = User(user_id=uuid4(), full_name="Patient", role=UserRole.ELDERLY_PATIENT)
    own_patient_id = uuid4()

    assert resolve_list_patient_id(
        ScalarDatabase(own_patient_id), actor=actor, patient_id=None
    ) == own_patient_id
    with pytest.raises(ReminderAccessForbiddenError):
        resolve_list_patient_id(
            ScalarDatabase(own_patient_id), actor=actor, patient_id=uuid4()
        )


def test_reminder_caregiver_admin_scope_requires_a_visible_patient():
    for role in (UserRole.CAREGIVER, UserRole.CARE_ADMIN):
        actor = User(user_id=uuid4(), full_name=role.value, role=role)
        with pytest.raises(ReminderQueryValidationError):
            resolve_list_patient_id(ScalarDatabase(), actor=actor, patient_id=None)
        with pytest.raises(ReminderNotFoundError):
            resolve_list_patient_id(
                ScalarDatabase(None), actor=actor, patient_id=uuid4()
            )


def test_visible_patient_scope_query_includes_active_household_guards():
    actor = User(user_id=uuid4(), full_name="Caregiver", role=UserRole.CAREGIVER)
    compiled = str(visible_patient_ids(actor))

    assert "elderly_patients.archived_at IS NULL" in compiled
    assert "households.household_status" in compiled
    assert "households.archived_at IS NULL" in compiled
    assert "caregiver_patient_assignments.unassigned_at IS NULL" in compiled


def test_reminder_payload_uses_only_the_public_joined_fields():
    template = ReminderTemplate(
        reminder_template_id=uuid4(),
        patient_id=uuid4(),
        created_by_user_id=uuid4(),
        title="Morning medication",
        category=ReminderCategory.MEDICATION,
        priority=ReminderPriority.NORMAL,
        start_date=datetime(2026, 9, 11).date(),
        start_time=datetime(2026, 9, 11, 8).time(),
    )
    occurrence = ReminderOccurrence(
        reminder_occurrence_id=uuid4(),
        reminder_template_id=template.reminder_template_id,
        scheduled_at=datetime(2026, 9, 11, 8, tzinfo=timezone.utc),
        due_at=datetime(2026, 9, 11, 8, 30, tzinfo=timezone.utc),
        status=ReminderOccurrenceStatus.UPCOMING,
    )

    payload = reminder_occurrence_payload(occurrence, template)

    assert payload == {
        "reminder_occurrence_id": occurrence.reminder_occurrence_id,
        "reminder_template_id": template.reminder_template_id,
        "patient_id": template.patient_id,
        "title": "Morning medication",
        "instructions": None,
        "category": ReminderCategory.MEDICATION,
        "priority": ReminderPriority.NORMAL,
        "scheduled_at": occurrence.scheduled_at,
        "due_at": occurrence.due_at,
        "status": ReminderOccurrenceStatus.UPCOMING,
        "snooze_allowed": None,
        "default_snooze_minutes": None,
        "missed_after_minutes": None,
    }


def test_patient_action_requests_normalize_notes_and_validate_snooze_bounds():
    action_id = uuid4()
    assert ReminderCompleteRequest(
        client_action_id=action_id, note="  completed  "
    ).note == "completed"
    assert ReminderCompleteRequest(client_action_id=action_id, note="   ").note is None
    assert ReminderSnoozeRequest(
        client_action_id=action_id, snooze_minutes=1
    ).snooze_minutes == 1
    with pytest.raises(ValidationError):
        ReminderSnoozeRequest(client_action_id=action_id, snooze_minutes=0)
    with pytest.raises(ValidationError):
        ReminderCompleteRequest(client_action_id=action_id, note="x" * 1001)


@pytest.mark.parametrize(
    "request_type", [ReminderCaregiverCompleteRequest, ReminderCancelRequest]
)
def test_caregiver_mutation_requests_require_nonblank_normalized_notes(request_type):
    action_id = uuid4()
    assert request_type(client_action_id=action_id, note="  explanation  ").note == "explanation"
    for invalid in ("   ", "x" * 1001):
        with pytest.raises(ValidationError):
            request_type(client_action_id=action_id, note=invalid)
