"""Add client action idempotency identifier.

Revision ID: a6e1b3c4d5f6
Revises: d4e8f6a1b2c3
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "a6e1b3c4d5f6"
down_revision = "d4e8f6a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reminder_actions",
        sa.Column("client_action_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_reminder_actions_client_action_id",
        "reminder_actions",
        ["client_action_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_reminder_actions_client_action_id", table_name="reminder_actions"
    )
    op.drop_column("reminder_actions", "client_action_id")
