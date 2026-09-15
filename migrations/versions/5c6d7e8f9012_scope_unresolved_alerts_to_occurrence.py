"""Scope unresolved alerts to a physiological occurrence.

Revision ID: 5c6d7e8f9012
Revises: 3831689894ab
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "5c6d7e8f9012"
down_revision: str | None = "3831689894ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_alerts_unresolved_patient_condition",
        table_name="alerts",
    )
    op.create_index(
        "uq_alerts_unresolved_patient_condition_occurrence",
        "alerts",
        ["patient_id", "condition_key", "detected_at"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('ACTIVE'::alert_status, "
            "'ACKNOWLEDGED'::alert_status)"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_alerts_unresolved_patient_condition_occurrence",
        table_name="alerts",
    )
    op.create_index(
        "uq_alerts_unresolved_patient_condition",
        "alerts",
        ["patient_id", "condition_key"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('ACTIVE'::alert_status, "
            "'ACKNOWLEDGED'::alert_status)"
        ),
    )
