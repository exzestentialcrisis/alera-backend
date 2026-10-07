"""Add append-only reminder occurrence events.

Revision ID: b7c4e9a2d1f6
Revises: a9d2e4f6b8c1
Create Date: 2026-10-07 13:25:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c4e9a2d1f6"
down_revision: str | Sequence[str] | None = "a9d2e4f6b8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


event_type_enum = postgresql.ENUM(
    "CREATED",
    "NOTIFICATION_SENT",
    "SNOOZED",
    "COMPLETED",
    "COMPLETED_LATE",
    "COMPLETED_ON_BEHALF",
    "CANCELED",
    "MARKED_MISSED",
    name="reminder_occurrence_event_type_enum",
)

actor_role_enum = postgresql.ENUM(
    "CAREGIVER",
    "PATIENT",
    "SYSTEM",
    name="reminder_event_actor_role_enum",
)


def upgrade() -> None:
    bind = op.get_bind()
    event_type_enum.create(bind, checkfirst=True)
    actor_role_enum.create(bind, checkfirst=True)

    op.create_table(
        "reminder_occurrence_events",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "reminder_occurrence_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            postgresql.ENUM(
                "CREATED",
                "NOTIFICATION_SENT",
                "SNOOZED",
                "COMPLETED",
                "COMPLETED_LATE",
                "COMPLETED_ON_BEHALF",
                "CANCELED",
                "MARKED_MISSED",
                name="reminder_occurrence_event_type_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "actor_role",
            postgresql.ENUM(
                "CAREGIVER",
                "PATIENT",
                "SYSTEM",
                name="reminder_event_actor_role_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.user_id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reminder_occurrence_id"],
            ["reminder_occurrences.reminder_occurrence_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "idx_reminder_occurrence_events_occurrence_time",
        "reminder_occurrence_events",
        ["reminder_occurrence_id", "occurred_at", "event_id"],
    )
    op.create_index(
        "idx_reminder_occurrence_events_actor",
        "reminder_occurrence_events",
        ["actor_user_id"],
    )

    # Existing occurrences came from caregiver-authored templates. Use the
    # template creation time and creator requested by the API contract while
    # marking the synthetic entry explicitly as a migration backfill.
    op.get_bind().exec_driver_sql(
        """
        INSERT INTO reminder_occurrence_events (
            event_id,
            reminder_occurrence_id,
            event_type,
            occurred_at,
            actor_user_id,
            actor_role,
            note,
            metadata
        )
        SELECT
            md5(
                occurrence.reminder_occurrence_id::text
                || ':CREATED'
            )::uuid,
            occurrence.reminder_occurrence_id,
            'CREATED'::reminder_occurrence_event_type_enum,
            template.created_at,
            template.created_by_user_id,
            'CAREGIVER'::reminder_event_actor_role_enum,
            NULL,
            jsonb_build_object('backfilled', true)
        FROM reminder_occurrences AS occurrence
        JOIN reminder_templates AS template
          ON template.reminder_template_id =
             occurrence.reminder_template_id
        """
    )


def downgrade() -> None:
    op.drop_index(
        "idx_reminder_occurrence_events_actor",
        table_name="reminder_occurrence_events",
    )
    op.drop_index(
        "idx_reminder_occurrence_events_occurrence_time",
        table_name="reminder_occurrence_events",
    )
    op.drop_table("reminder_occurrence_events")

    bind = op.get_bind()
    actor_role_enum.drop(bind, checkfirst=True)
    event_type_enum.drop(bind, checkfirst=True)
