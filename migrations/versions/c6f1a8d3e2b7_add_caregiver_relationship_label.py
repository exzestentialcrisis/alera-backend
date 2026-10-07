"""Add caregiver-specific patient relationship labels.

Revision ID: c6f1a8d3e2b7
Revises: b7c4e9a2d1f6
Create Date: 2026-10-07 15:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6f1a8d3e2b7"
down_revision: str | Sequence[str] | None = "b7c4e9a2d1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "caregiver_patient_assignments",
        sa.Column(
            "relationship_label",
            sa.String(length=50),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "caregiver_patient_assignments",
        "relationship_label",
    )
