"""Create reminder schema.

Revision ID: d4e8f6a1b2c3
Revises: c8d4e52f6b91
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "d4e8f6a1b2c3"
down_revision = "c8d4e52f6b91"
branch_labels = None
depends_on = None


reminder_category_enum = postgresql.ENUM(
    "MEDICATION", "HEALTH_CHECK", "HYDRATION", "MEAL", "MOBILITY",
    "APPOINTMENT", "CHECK_IN", "DEVICE_TASK", "OTHER",
    name="reminder_category_enum",
)
reminder_priority_enum = postgresql.ENUM(
    "LOW", "NORMAL", "HIGH", name="reminder_priority_enum"
)
reminder_template_status_enum = postgresql.ENUM(
    "ACTIVE", "DISABLED", "ARCHIVED", name="reminder_template_status_enum"
)
reminder_occurrence_status_enum = postgresql.ENUM(
    "UPCOMING", "DUE", "SNOOZED", "COMPLETED", "MISSED", "CANCELED",
    "COMPLETED_LATE", name="reminder_occurrence_status_enum",
)
reminder_action_type_enum = postgresql.ENUM(
    "MARK_COMPLETED", "SNOOZE", "REQUEST_HELP", "CAREGIVER_OVERRIDE",
    "MARK_MISSED", "MARK_MISSED_HANDLED", "RESCHEDULE", "CANCEL",
    "ADD_NOTE", "FOLLOW_UP", name="reminder_action_type_enum",
)
reminder_notification_channel_enum = postgresql.ENUM(
    "IN_APP", "PUSH", "SMS", name="reminder_notification_channel_enum"
)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    reminder_category_enum.create(bind, checkfirst=False)
    reminder_priority_enum.create(bind, checkfirst=False)
    reminder_template_status_enum.create(bind, checkfirst=False)
    reminder_occurrence_status_enum.create(bind, checkfirst=False)
    reminder_action_type_enum.create(bind, checkfirst=False)
    reminder_notification_channel_enum.create(bind, checkfirst=False)

    op.create_table(
        "reminder_templates",
        sa.Column(
            "reminder_template_id", postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("category", _enum("reminder_category_enum"), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column(
            "priority", _enum("reminder_priority_enum"),
            server_default=sa.text("'NORMAL'::reminder_priority_enum"), nullable=False,
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(timezone=False), nullable=False),
        sa.Column("schedule_rule", sa.Text(), nullable=True),
        sa.Column("snooze_allowed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("default_snooze_minutes", sa.SmallInteger(), server_default=sa.text("10"), nullable=False),
        sa.Column("missed_after_minutes", sa.SmallInteger(), server_default=sa.text("30"), nullable=False),
        sa.Column(
            "notification_channels", _enum("reminder_notification_channel_enum"),
            server_default=sa.text("'IN_APP'::reminder_notification_channel_enum"), nullable=False,
        ),
        sa.Column(
            "status", _enum("reminder_template_status_enum"),
            server_default=sa.text("'ACTIVE'::reminder_template_status_enum"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("default_snooze_minutes >= 0", name="reminder_default_snooze_nonnegative"),
        sa.CheckConstraint("missed_after_minutes >= 0", name="reminder_missed_after_nonnegative"),
        sa.ForeignKeyConstraint(["patient_id"], ["elderly_patients.patient_id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("reminder_template_id"),
    )
    op.create_table(
        "reminder_occurrences",
        sa.Column(
            "reminder_occurrence_id", postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ),
        sa.Column("reminder_template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", _enum("reminder_occurrence_status_enum"),
            server_default=sa.text("'UPCOMING'::reminder_occurrence_status_enum"), nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["reminder_template_id"], ["reminder_templates.reminder_template_id"]),
        sa.PrimaryKeyConstraint("reminder_occurrence_id"),
    )
    op.create_table(
        "reminder_actions",
        sa.Column(
            "reminder_action_id", postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"), nullable=False,
        ),
        sa.Column("reminder_occurrence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("performed_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", _enum("reminder_action_type_enum"), nullable=False),
        sa.Column("action_note", sa.Text(), nullable=True),
        sa.Column("previous_status", _enum("reminder_occurrence_status_enum"), nullable=True),
        sa.Column("new_status", _enum("reminder_occurrence_status_enum"), nullable=True),
        sa.Column("new_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("performed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["reminder_occurrence_id"], ["reminder_occurrences.reminder_occurrence_id"]),
        sa.ForeignKeyConstraint(["performed_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("reminder_action_id"),
    )

    op.create_index("idx_reminder_templates_created_by", "reminder_templates", ["created_by_user_id"])
    op.create_index("idx_reminder_templates_patient_id", "reminder_templates", ["patient_id"])
    op.create_index("idx_reminder_templates_status", "reminder_templates", ["status"])
    op.create_index("idx_reminder_occurrences_due_at", "reminder_occurrences", ["due_at"])
    op.create_index("idx_reminder_occurrences_status", "reminder_occurrences", ["status"])
    op.create_index("idx_reminder_occurrences_template_id", "reminder_occurrences", ["reminder_template_id"])
    op.create_index("idx_reminder_actions_occurrence_id", "reminder_actions", ["reminder_occurrence_id"])
    op.create_index("idx_reminder_actions_performed_by", "reminder_actions", ["performed_by_user_id"])


def downgrade() -> None:
    op.drop_index("idx_reminder_actions_performed_by", table_name="reminder_actions")
    op.drop_index("idx_reminder_actions_occurrence_id", table_name="reminder_actions")
    op.drop_table("reminder_actions")
    op.drop_index("idx_reminder_occurrences_template_id", table_name="reminder_occurrences")
    op.drop_index("idx_reminder_occurrences_status", table_name="reminder_occurrences")
    op.drop_index("idx_reminder_occurrences_due_at", table_name="reminder_occurrences")
    op.drop_table("reminder_occurrences")
    op.drop_index("idx_reminder_templates_status", table_name="reminder_templates")
    op.drop_index("idx_reminder_templates_patient_id", table_name="reminder_templates")
    op.drop_index("idx_reminder_templates_created_by", table_name="reminder_templates")
    op.drop_table("reminder_templates")

    bind = op.get_bind()
    reminder_notification_channel_enum.drop(bind, checkfirst=False)
    reminder_action_type_enum.drop(bind, checkfirst=False)
    reminder_occurrence_status_enum.drop(bind, checkfirst=False)
    reminder_template_status_enum.drop(bind, checkfirst=False)
    reminder_priority_enum.drop(bind, checkfirst=False)
    reminder_category_enum.drop(bind, checkfirst=False)
