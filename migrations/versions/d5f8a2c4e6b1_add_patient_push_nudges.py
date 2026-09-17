"""Add patient push registrations and caregiver nudges.

Revision ID: d5f8a2c4e6b1
Revises: c3e7f9a1b5d2
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d5f8a2c4e6b1"
down_revision: str | None = "c3e7f9a1b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    nudge_type = postgresql.ENUM(
        "DRINK_WATER",
        "TAKE_MEDICATION",
        "CHECK_BLOOD_PRESSURE",
        name="patient_nudge_type",
    )
    nudge_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "patient_push_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fcm_token", sa.String(2048), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "platform = 'ANDROID'", name="ck_patient_push_devices_platform"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fcm_token"),
    )
    op.create_index(
        "ix_patient_push_devices_user_id", "patient_push_devices", ["user_id"]
    )
    op.create_table(
        "patient_nudges",
        sa.Column("nudge_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sent_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "nudge_type",
            postgresql.ENUM(name="patient_nudge_type", create_type=False),
            nullable=False,
        ),
        sa.Column("client_action_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["patient_id"], ["elderly_patients.patient_id"]),
        sa.ForeignKeyConstraint(["sent_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("nudge_id"),
        sa.UniqueConstraint("client_action_id"),
    )
    op.create_index(
        "ix_patient_nudges_patient_created",
        "patient_nudges",
        ["patient_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_patient_nudges_patient_created", table_name="patient_nudges")
    op.drop_table("patient_nudges")
    op.drop_index("ix_patient_push_devices_user_id", table_name="patient_push_devices")
    op.drop_table("patient_push_devices")
    postgresql.ENUM(name="patient_nudge_type").drop(op.get_bind(), checkfirst=True)
