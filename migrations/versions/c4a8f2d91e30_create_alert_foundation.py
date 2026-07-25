"""create alert database foundation

Revision ID: c4a8f2d91e30
Revises: b86c2c0d44f1
Create Date: 2026-07-25

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4a8f2d91e30"
down_revision: Union[str, Sequence[str], None] = "b86c2c0d44f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


alert_status = postgresql.ENUM(
    "ACTIVE",
    "ACKNOWLEDGED",
    "RESOLVED",
    "FALSE_ALARM",
    "ARCHIVED",
    name="alert_status",
)
alert_action_type = postgresql.ENUM(
    "ACKNOWLEDGE",
    "RESOLVE",
    "ESCALATE",
    "MARK_FALSE_ALARM",
    "ADD_NOTE",
    "LOG_INTERVENTION",
    name="alert_action_type",
)


def upgrade() -> None:
    bind = op.get_bind()
    alert_status.create(bind, checkfirst=True)
    alert_action_type.create(bind, checkfirst=True)

    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column(
            "condition_key",
            postgresql.ENUM(name="condition_key", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "severity",
            postgresql.ENUM(name="evaluation_severity", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="alert_status", create_type=False),
            server_default=sa.text("'ACTIVE'::alert_status"),
            nullable=False,
        ),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confirmed_at >= detected_at",
            name="ck_alerts_confirmed_at_after_detected_at",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= confirmed_at",
            name="ck_alerts_resolved_at_after_confirmed_at",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["elderly_patients.patient_id"],
            name="fk_alerts_patient_id_elderly_patients",
        ),
        sa.PrimaryKeyConstraint("alert_id"),
    )
    op.create_index(
        "ix_alerts_patient_status",
        "alerts",
        ["patient_id", "status"],
    )
    op.create_index(
        "ix_alerts_patient_condition_detected",
        "alerts",
        ["patient_id", "condition_key", "detected_at"],
    )
    op.create_index("ix_alerts_detected_at", "alerts", ["detected_at"])
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

    op.create_table(
        "alert_actions",
        sa.Column("alert_action_id", sa.UUID(), nullable=False),
        sa.Column("alert_id", sa.UUID(), nullable=False),
        sa.Column("performed_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "action_type",
            postgresql.ENUM(name="alert_action_type", create_type=False),
            nullable=False,
        ),
        sa.Column("action_note", sa.Text(), nullable=True),
        sa.Column(
            "previous_status",
            postgresql.ENUM(name="alert_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "new_status",
            postgresql.ENUM(name="alert_status", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.alert_id"],
            name="fk_alert_actions_alert_id_alerts",
        ),
        sa.ForeignKeyConstraint(
            ["performed_by_user_id"],
            ["users.user_id"],
            name="fk_alert_actions_performed_by_user_id_users",
        ),
        sa.PrimaryKeyConstraint("alert_action_id"),
    )
    op.create_index(
        "ix_alert_actions_alert_performed_at",
        "alert_actions",
        ["alert_id", "performed_at"],
    )

    op.create_foreign_key(
        "fk_event_evaluations_alert_id_alerts",
        "event_evaluations",
        "alerts",
        ["alert_id"],
        ["alert_id"],
    )
    op.create_index(
        "ix_event_evaluations_alert_id",
        "event_evaluations",
        ["alert_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_evaluations_alert_id",
        table_name="event_evaluations",
    )
    op.drop_constraint(
        "fk_event_evaluations_alert_id_alerts",
        "event_evaluations",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_alert_actions_alert_performed_at",
        table_name="alert_actions",
    )
    op.drop_table("alert_actions")
    op.drop_index(
        "uq_alerts_unresolved_patient_condition",
        table_name="alerts",
    )
    op.drop_index("ix_alerts_detected_at", table_name="alerts")
    op.drop_index(
        "ix_alerts_patient_condition_detected",
        table_name="alerts",
    )
    op.drop_index("ix_alerts_patient_status", table_name="alerts")
    op.drop_table("alerts")

    bind = op.get_bind()
    alert_action_type.drop(bind, checkfirst=True)
    alert_status.drop(bind, checkfirst=True)
