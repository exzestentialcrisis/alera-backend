"""create help requests

Revision ID: f4c1b7a9d203
Revises: ded8b03f57f9
Create Date: 2026-10-04 23:10:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4c1b7a9d203"
down_revision: Union[str, Sequence[str], None] = "ded8b03f57f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    status_enum = postgresql.ENUM(
        "PENDING",
        "ACKNOWLEDGED",
        "RESOLVED",
        name="help_request_status",
    )
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "help_requests",
        sa.Column(
            "help_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "ACKNOWLEDGED",
                "RESOLVED",
                name="help_request_status",
                create_type=False,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column(
            "client_action_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "acknowledged_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "resolved_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["users.user_id"],
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["elderly_patients.patient_id"],
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.user_id"],
        ),
        sa.PrimaryKeyConstraint("help_request_id"),
        sa.UniqueConstraint(
            "client_action_id",
            name="uq_help_requests_client_action_id",
        ),
    )
    op.create_index(
        "ix_help_requests_patient_requested",
        "help_requests",
        ["patient_id", "requested_at"],
    )
    op.create_index(
        "uq_help_requests_patient_unresolved",
        "help_requests",
        ["patient_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PENDING'::help_request_status, "
            "'ACKNOWLEDGED'::help_request_status)"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_help_requests_patient_unresolved",
        table_name="help_requests",
    )
    op.drop_index(
        "ix_help_requests_patient_requested",
        table_name="help_requests",
    )
    op.drop_table("help_requests")

    postgresql.ENUM(
        "PENDING",
        "ACKNOWLEDGED",
        "RESOLVED",
        name="help_request_status",
    ).drop(op.get_bind(), checkfirst=True)
