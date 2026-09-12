from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from app.reminders.enums import (
    ReminderActionType,
    ReminderCategory,
    ReminderNotificationChannel,
    ReminderOccurrenceStatus,
    ReminderPriority,
    ReminderTemplateStatus,
)
from app.reminders.model import ReminderAction, ReminderOccurrence, ReminderTemplate


def _foreign_key_targets(model):
    return {foreign_key.target_fullname for foreign_key in model.__table__.foreign_keys}


def _index_names(model):
    return {index.name for index in model.__table__.indexes}


def test_reminder_tables_columns_and_foreign_keys():
    assert ReminderTemplate.__tablename__ == "reminder_templates"
    assert ReminderOccurrence.__tablename__ == "reminder_occurrences"
    assert ReminderAction.__tablename__ == "reminder_actions"

    required_template_columns = {
        "reminder_template_id", "patient_id", "created_by_user_id", "title", "category",
        "priority", "start_date", "start_time", "snooze_allowed", "default_snooze_minutes",
        "missed_after_minutes", "notification_channels", "status", "created_at", "updated_at",
    }
    assert all(not ReminderTemplate.__table__.c[name].nullable for name in required_template_columns)
    assert all(ReminderTemplate.__table__.c[name].nullable for name in ("instructions", "schedule_rule", "archived_at"))
    assert _foreign_key_targets(ReminderTemplate) == {
        "elderly_patients.patient_id", "users.user_id"
    }

    required_occurrence_columns = {
        "reminder_occurrence_id", "reminder_template_id", "scheduled_at", "due_at",
        "status", "created_at", "updated_at",
    }
    assert all(not ReminderOccurrence.__table__.c[name].nullable for name in required_occurrence_columns)
    assert _foreign_key_targets(ReminderOccurrence) == {"reminder_templates.reminder_template_id"}

    required_action_columns = {
        "reminder_action_id", "reminder_occurrence_id", "performed_by_user_id", "action_type", "performed_at",
    }
    assert all(not ReminderAction.__table__.c[name].nullable for name in required_action_columns)
    assert all(ReminderAction.__table__.c[name].nullable for name in ("action_note", "previous_status", "new_status", "new_due_at", "metadata", "client_action_id"))
    assert isinstance(ReminderAction.__table__.c.metadata.type, JSONB)
    assert isinstance(ReminderAction.__table__.c.client_action_id.type, UUID)
    assert ReminderAction.__table__.c.client_action_id.type.as_uuid is True
    assert ReminderAction.__table__.c.client_action_id.default is None
    assert ReminderAction.__table__.c.client_action_id.server_default is None
    assert _foreign_key_targets(ReminderAction) == {
        "reminder_occurrences.reminder_occurrence_id", "users.user_id"
    }


def test_reminder_enums_map_to_existing_native_postgresql_types():
    expected = {
        ReminderTemplate.__table__.c.category: "reminder_category_enum",
        ReminderTemplate.__table__.c.priority: "reminder_priority_enum",
        ReminderTemplate.__table__.c.notification_channels: "reminder_notification_channel_enum",
        ReminderTemplate.__table__.c.status: "reminder_template_status_enum",
        ReminderOccurrence.__table__.c.status: "reminder_occurrence_status_enum",
        ReminderAction.__table__.c.action_type: "reminder_action_type_enum",
        ReminderAction.__table__.c.previous_status: "reminder_occurrence_status_enum",
        ReminderAction.__table__.c.new_status: "reminder_occurrence_status_enum",
    }

    for column, enum_name in expected.items():
        assert isinstance(column.type, ENUM)
        assert column.type.name == enum_name
        assert column.type.create_type is False


def test_reminder_server_defaults_constraints_and_indexes():
    template = ReminderTemplate.__table__
    occurrence = ReminderOccurrence.__table__
    action = ReminderAction.__table__

    assert str(template.c.priority.server_default.arg) == "NORMAL"
    assert str(template.c.snooze_allowed.server_default.arg) == "true"
    assert str(template.c.default_snooze_minutes.server_default.arg) == "10"
    assert str(template.c.missed_after_minutes.server_default.arg) == "30"
    assert str(template.c.notification_channels.server_default.arg) == "IN_APP"
    assert str(template.c.status.server_default.arg) == "ACTIVE"
    assert str(occurrence.c.status.server_default.arg) == "UPCOMING"
    for column in (template.c.created_at, template.c.updated_at, occurrence.c.created_at, occurrence.c.updated_at, action.c.performed_at):
        assert str(column.server_default.arg) == "now()"

    constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in template.constraints
        if constraint.name is not None
    }
    assert constraints["reminder_default_snooze_nonnegative"] == (
        "default_snooze_minutes >= 0"
    )
    assert constraints["reminder_missed_after_nonnegative"] == (
        "missed_after_minutes >= 0"
    )
    assert _index_names(ReminderTemplate) == {
        "idx_reminder_templates_created_by", "idx_reminder_templates_patient_id", "idx_reminder_templates_status"
    }
    assert _index_names(ReminderOccurrence) == {
        "idx_reminder_occurrences_due_at", "idx_reminder_occurrences_status", "idx_reminder_occurrences_template_id"
    }
    assert _index_names(ReminderAction) == {
        "idx_reminder_actions_occurrence_id", "idx_reminder_actions_performed_by",
        "uq_reminder_actions_client_action_id",
    }
    client_action_index = next(
        index
        for index in action.indexes
        if index.name == "uq_reminder_actions_client_action_id"
    )
    assert client_action_index.unique is True
    assert [column.name for column in client_action_index.columns] == [
        "client_action_id"
    ]


def test_reminder_python_enum_values_match_database_values():
    assert [item.value for item in ReminderCategory] == ["MEDICATION", "HEALTH_CHECK", "HYDRATION", "MEAL", "MOBILITY", "APPOINTMENT", "CHECK_IN", "DEVICE_TASK", "OTHER"]
    assert [item.value for item in ReminderPriority] == ["LOW", "NORMAL", "HIGH"]
    assert [item.value for item in ReminderTemplateStatus] == ["ACTIVE", "DISABLED", "ARCHIVED"]
    assert [item.value for item in ReminderOccurrenceStatus] == ["UPCOMING", "DUE", "SNOOZED", "COMPLETED", "MISSED", "CANCELED", "COMPLETED_LATE"]
    assert [item.value for item in ReminderActionType] == ["MARK_COMPLETED", "SNOOZE", "REQUEST_HELP", "CAREGIVER_OVERRIDE", "MARK_MISSED", "MARK_MISSED_HANDLED", "RESCHEDULE", "CANCEL", "ADD_NOTE", "FOLLOW_UP"]
    assert [item.value for item in ReminderNotificationChannel] == ["IN_APP", "PUSH", "SMS"]
