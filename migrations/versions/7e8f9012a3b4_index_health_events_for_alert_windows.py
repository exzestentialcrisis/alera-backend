"""Index health events for alert observation windows.

Revision ID: 7e8f9012a3b4
Revises: 5c6d7e8f9012
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "7e8f9012a3b4"
down_revision: str | None = "5c6d7e8f9012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_health_events_patient_metric_recorded",
        "health_events",
        ["patient_id", "metric_type", "recorded_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_health_events_patient_metric_recorded",
        table_name="health_events",
    )
