"""Add the automatic due-reminder audit action.

Revision ID: e7a4c2d9f106
Revises: d5f8a2c4e6b1, cd660c231d72
"""

from collections.abc import Sequence

from alembic import op


revision: str = "e7a4c2d9f106"
down_revision: tuple[str, str] = ("d5f8a2c4e6b1", "cd660c231d72")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE reminder_action_type_enum "
        "ADD VALUE IF NOT EXISTS 'MARK_DUE' BEFORE 'MARK_COMPLETED'"
    )


def downgrade() -> None:
    # PostgreSQL cannot remove one enum value safely in place. MARK_DUE remains
    # until the original reminder enum is dropped by its owning migration.
    pass
