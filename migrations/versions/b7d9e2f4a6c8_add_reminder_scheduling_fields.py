"""Add reminder scheduling fields.

Revision ID: b7d9e2f4a6c8
Revises: a6e1b3c4d5f6
"""

import sqlalchemy as sa
from alembic import op


revision = "b7d9e2f4a6c8"
down_revision = "a6e1b3c4d5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reminder_templates",
        sa.Column(
            "timezone",
            sa.String(),
            nullable=True,
            server_default=sa.text("'Asia/Manila'"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE reminder_templates SET timezone = 'Asia/Manila' "
            "WHERE timezone IS NULL"
        )
    )
    op.alter_column(
        "reminder_templates",
        "timezone",
        existing_type=sa.String(),
        nullable=False,
        server_default=None,
    )
    op.add_column(
        "reminder_templates",
        sa.Column(
            "due_after_minutes",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("15"),
        ),
    )
    op.create_check_constraint(
        "reminder_due_after_nonnegative",
        "reminder_templates",
        "due_after_minutes >= 0",
    )
    op.create_index(
        "uq_reminder_occurrences_template_scheduled_at",
        "reminder_occurrences",
        ["reminder_template_id", "scheduled_at"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reminder_occurrences_template_scheduled_at",
        table_name="reminder_occurrences",
    )
    op.drop_constraint(
        "reminder_due_after_nonnegative",
        "reminder_templates",
        type_="check",
    )
    op.drop_column("reminder_templates", "due_after_minutes")
    op.drop_column("reminder_templates", "timezone")
