"""Add caregiver FCM registrations.

Revision ID: e2c7f19a6b40
Revises: a91f0c3d8e72
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e2c7f19a6b40"
down_revision = "a91f0c3d8e72"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "caregiver_push_devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fcm_token", sa.String(2048), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("platform = 'ANDROID'", name="ck_push_devices_platform"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fcm_token"),
    )
    op.create_index(
        "ix_caregiver_push_devices_user_id", "caregiver_push_devices", ["user_id"]
    )


def downgrade():
    op.drop_index(
        "ix_caregiver_push_devices_user_id", table_name="caregiver_push_devices"
    )
    op.drop_table("caregiver_push_devices")
