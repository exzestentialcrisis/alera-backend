"""Add append-only caregiver notes to help requests.

Revision ID: a9d2e4f6b8c1
Revises: f4c1b7a9d203
Create Date: 2026-10-06 19:20:00

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9d2e4f6b8c1"
down_revision: str | Sequence[str] | None = "f4c1b7a9d203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "help_request_notes",
        sa.Column(
            "help_request_note_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "help_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "client_action_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["help_request_id"],
            ["help_requests.help_request_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("help_request_note_id"),
        sa.UniqueConstraint(
            "client_action_id",
            name="uq_help_request_notes_client_action_id",
        ),
    )
    op.create_index(
        "ix_help_request_notes_request_created",
        "help_request_notes",
        ["help_request_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_help_request_notes_request_created",
        table_name="help_request_notes",
    )
    op.drop_table("help_request_notes")
