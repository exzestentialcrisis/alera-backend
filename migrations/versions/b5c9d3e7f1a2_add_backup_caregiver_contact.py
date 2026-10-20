"""add backup caregiver contact to patients

Revision ID: b5c9d3e7f1a2
Revises: a4b8c2d6e0f1
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5c9d3e7f1a2"
down_revision: str | None = "a4b8c2d6e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "elderly_patients",
        sa.Column("backup_caregiver_name", sa.String(length=150), nullable=True),
    )
    op.add_column(
        "elderly_patients",
        sa.Column("backup_caregiver_phone", sa.String(length=30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("elderly_patients", "backup_caregiver_phone")
    op.drop_column("elderly_patients", "backup_caregiver_name")
