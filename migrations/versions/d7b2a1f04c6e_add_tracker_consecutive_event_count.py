"""add tracker consecutive event count

Revision ID: d7b2a1f04c6e
Revises: c4a8f2d91e30
Create Date: 2026-07-27

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7b2a1f04c6e"
down_revision: Union[str, Sequence[str], None] = "c4a8f2d91e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "condition_trackers",
        sa.Column(
            "consecutive_event_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("condition_trackers", "consecutive_event_count")
