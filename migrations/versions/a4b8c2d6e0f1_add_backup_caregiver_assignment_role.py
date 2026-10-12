"""Add primary and backup caregiver assignment roles.

Revision ID: a4b8c2d6e0f1
Revises: c6f1a8d3e2b7
Create Date: 2026-10-12 23:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4b8c2d6e0f1"
down_revision: str | Sequence[str] | None = "c6f1a8d3e2b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "caregiver_patient_assignments",
        sa.Column(
            "assignment_role",
            sa.String(length=20),
            nullable=False,
            server_default="PRIMARY",
        ),
    )
    op.create_check_constraint(
        "ck_caregiver_patient_assignments_role",
        "caregiver_patient_assignments",
        "assignment_role IN ('PRIMARY', 'BACKUP')",
    )
    op.create_index(
        "uq_caregiver_patient_assignments_active_backup",
        "caregiver_patient_assignments",
        ["patient_id"],
        unique=True,
        postgresql_where=sa.text(
            "unassigned_at IS NULL AND assignment_role = 'BACKUP'"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_caregiver_patient_assignments_active_backup",
        table_name="caregiver_patient_assignments",
    )
    op.drop_constraint(
        "ck_caregiver_patient_assignments_role",
        "caregiver_patient_assignments",
        type_="check",
    )
    op.drop_column("caregiver_patient_assignments", "assignment_role")
