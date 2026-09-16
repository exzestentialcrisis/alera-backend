"""Allow reminder actions performed automatically by the system.

Revision ID: c3e7f9a1b5d2
Revises: 9a4c6e8f1b2d
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c3e7f9a1b5d2"
down_revision: str | None = "9a4c6e8f1b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "reminder_actions",
        "performed_by_user_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM reminder_actions WHERE performed_by_user_id IS NULL"
            ")"
        )
    ):
        raise RuntimeError(
            "Cannot require reminder_actions.performed_by_user_id while system "
            "reminder actions exist."
        )
    op.alter_column(
        "reminder_actions",
        "performed_by_user_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
