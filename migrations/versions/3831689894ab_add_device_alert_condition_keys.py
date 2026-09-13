"""add device alert condition keys

Revision ID: 3831689894ab
Revises: f921010dbd93
Create Date: 2026-09-13 12:26:15.178721

"""
from typing import Sequence, Union

from alembic import op

revision: str = '3831689894ab'
down_revision: Union[str, Sequence[str], None] = 'f921010dbd93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.execute(
        "ALTER TYPE condition_key "
        "ADD VALUE IF NOT EXISTS 'PHONE_DISCONNECTED'"
    )

    op.execute(
        "ALTER TYPE condition_key "
        "ADD VALUE IF NOT EXISTS 'WATCH_DISCONNECTED'"
    )

    op.execute(
        "ALTER TYPE condition_key "
        "ADD VALUE IF NOT EXISTS 'PHONE_BATTERY_LOW'"
    )

    op.execute(
        "ALTER TYPE condition_key "
        "ADD VALUE IF NOT EXISTS 'WATCH_BATTERY_LOW'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    pass