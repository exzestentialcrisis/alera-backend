"""add activity data and sessions

Revision ID: cd660c231d72
Revises: 9a4c6e8f1b2d
Create Date: 2026-09-17 11:59:47.989620

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'cd660c231d72'
down_revision: Union[str, Sequence[str], None] = '9a4c6e8f1b2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    activity_type = postgresql.ENUM(
        "STEPS",
        "WALKING",
        "RUNNING",
        "EXERCISE",
        "SLEEP",
        name="activity_type",
        create_type=False,
    )

    sleep_type = postgresql.ENUM(
        "MAIN",
        "NAP",
        "UNKNOWN",
        name="sleep_type",
        create_type=False,
    )

    activity_type.create(
        op.get_bind(),
        checkfirst=True,
    )

    sleep_type.create(
        op.get_bind(),
        checkfirst=True,
    )

    op.create_table(
        "activity_data",
        sa.Column(
            "activity_data_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "activity_date",
            sa.Date(),
            nullable=False,
        ),
        sa.Column(
            "activity_type",
            activity_type,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["elderly_patients.patient_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "activity_data_id",
        ),
        sa.UniqueConstraint(
            "patient_id",
            "activity_date",
            "activity_type",
            name="uq_activity_data_patient_date_type",
        ),
    )

    op.create_index(
        "ix_activity_data_patient_date",
        "activity_data",
        [
            "patient_id",
            "activity_date",
        ],
        unique=False,
    )

    op.create_table(
        "activity_daily_data",
        sa.Column(
            "activity_daily_data_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "activity_data_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "total_steps",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "total_duration_seconds",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "total_distance_meters",
            sa.Numeric(
                precision=12,
                scale=2,
            ),
            nullable=True,
        ),

        sa.Column(
            "session_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "first_movement_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_movement_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "total_steps IS NULL OR total_steps >= 0",
            name="ck_activity_daily_steps_nonnegative",
        ),
        sa.CheckConstraint(
            "total_duration_seconds >= 0",
            name="ck_activity_daily_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "session_count >= 0",
            name="ck_activity_daily_session_count_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["activity_data_id"],
            ["activity_data.activity_data_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "activity_daily_data_id",
        ),
        sa.UniqueConstraint(
            "activity_data_id",
        ),
    )

    op.create_index(
        "ix_activity_daily_last_movement",
        "activity_daily_data",
        ["last_movement_at"],
        unique=False,
    )

    op.create_table(
        "activity_sessions",
        sa.Column(
            "activity_session_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "activity_data_id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "external_session_id",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "sleep_type",
            sleep_type,
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "ended_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "duration_seconds",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "steps",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column(
            "distance_meters",
            sa.Numeric(
                precision=12,
                scale=2,
            ),
            nullable=True,
        ),
        
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_activity_sessions_end_after_start",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_activity_sessions_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "steps IS NULL OR steps >= 0",
            name="ck_activity_sessions_steps_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["activity_data_id"],
            ["activity_data.activity_data_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "activity_session_id",
        ),
        sa.UniqueConstraint(
            "activity_data_id",
            "external_session_id",
            name="uq_activity_sessions_external_session",
        ),
    )

    op.create_index(
        "ix_activity_sessions_activity_started",
        "activity_sessions",
        [
            "activity_data_id",
            "started_at",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_activity_sessions_activity_started",
        table_name="activity_sessions",
    )

    op.drop_table(
        "activity_sessions",
    )

    op.drop_index(
        "ix_activity_daily_last_movement",
        table_name="activity_daily_data",
    )

    op.drop_table(
        "activity_daily_data",
    )

    op.drop_index(
        "ix_activity_data_patient_date",
        table_name="activity_data",
    )

    op.drop_table(
        "activity_data",
    )

    sleep_type = postgresql.ENUM(
        name="sleep_type",
    )

    activity_type = postgresql.ENUM(
        name="activity_type",
    )

    sleep_type.drop(
        op.get_bind(),
        checkfirst=True,
    )

    activity_type.drop(
        op.get_bind(),
        checkfirst=True,
    )
