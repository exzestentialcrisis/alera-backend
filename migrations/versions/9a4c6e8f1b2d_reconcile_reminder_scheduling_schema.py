"""Reconcile reminder scheduling schema.

Revision ID: 9a4c6e8f1b2d
Revises: 7e8f9012a3b4
Create Date: 2026-09-16 00:00:00.000000

This migration adopts an already-correct production schema while still
creating the scheduling contract on clean databases.  It intentionally does
not reference the unmerged b7d9e2f4a6c8 revision.
"""

from collections.abc import Sequence

from alembic import op


revision: str = "9a4c6e8f1b2d"
down_revision: str | None = "7e8f9012a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $reconcile_timezone$
        DECLARE
            existing_type text;
        BEGIN
            SELECT format_type(attribute.atttypid, attribute.atttypmod)
              INTO existing_type
              FROM pg_attribute AS attribute
              JOIN pg_class AS relation ON relation.oid = attribute.attrelid
              JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = current_schema()
               AND relation.relname = 'reminder_templates'
               AND attribute.attname = 'timezone'
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped;

            IF NOT FOUND THEN
                ALTER TABLE reminder_templates ADD COLUMN timezone VARCHAR;
            ELSIF existing_type <> 'character varying' THEN
                RAISE EXCEPTION
                    'reminder_templates.timezone has incompatible type: %',
                    existing_type;
            END IF;
        END
        $reconcile_timezone$;
        """
    )
    op.execute(
        "UPDATE reminder_templates "
        "SET timezone = 'Asia/Manila' WHERE timezone IS NULL"
    )
    op.execute(
        "ALTER TABLE reminder_templates ALTER COLUMN timezone SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE reminder_templates ALTER COLUMN timezone DROP DEFAULT"
    )

    op.execute(
        """
        DO $reconcile_due_after$
        DECLARE
            existing_type text;
        BEGIN
            SELECT format_type(attribute.atttypid, attribute.atttypmod)
              INTO existing_type
              FROM pg_attribute AS attribute
              JOIN pg_class AS relation ON relation.oid = attribute.attrelid
              JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = current_schema()
               AND relation.relname = 'reminder_templates'
               AND attribute.attname = 'due_after_minutes'
               AND attribute.attnum > 0
               AND NOT attribute.attisdropped;

            IF NOT FOUND THEN
                ALTER TABLE reminder_templates
                    ADD COLUMN due_after_minutes SMALLINT;
            ELSIF existing_type <> 'smallint' THEN
                RAISE EXCEPTION
                    'reminder_templates.due_after_minutes has incompatible type: %',
                    existing_type;
            END IF;
        END
        $reconcile_due_after$;
        """
    )
    op.execute(
        "UPDATE reminder_templates "
        "SET due_after_minutes = 15 WHERE due_after_minutes IS NULL"
    )
    op.execute(
        "ALTER TABLE reminder_templates "
        "ALTER COLUMN due_after_minutes SET DEFAULT 15"
    )
    op.execute(
        "ALTER TABLE reminder_templates "
        "ALTER COLUMN due_after_minutes SET NOT NULL"
    )

    op.execute(
        """
        DO $reconcile_due_check$
        DECLARE
            existing_definition text;
            existing_table text;
            existing_type "char";
        BEGIN
            SELECT pg_get_constraintdef(constraint_row.oid),
                   relation.relname,
                   constraint_row.contype
              INTO existing_definition, existing_table, existing_type
              FROM pg_constraint AS constraint_row
              JOIN pg_class AS relation
                ON relation.oid = constraint_row.conrelid
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = current_schema()
               AND constraint_row.conname = 'reminder_due_after_nonnegative';

            IF NOT FOUND THEN
                ALTER TABLE reminder_templates
                    ADD CONSTRAINT reminder_due_after_nonnegative
                    CHECK (due_after_minutes >= 0);
            ELSIF existing_table <> 'reminder_templates'
               OR existing_type <> 'c'
               OR regexp_replace(
                    lower(existing_definition),
                    '[[:space:]()]',
                    '',
                    'g'
                  ) <> 'checkdue_after_minutes>=0' THEN
                RAISE EXCEPTION
                    'reminder_due_after_nonnegative has incompatible definition: %',
                    existing_definition;
            END IF;
        END
        $reconcile_due_check$;
        """
    )

    op.execute(
        """
        DO $reconcile_occurrence_index$
        DECLARE
            existing_unique boolean;
            existing_table text;
            existing_columns text[];
            existing_predicate text;
            existing_expressions text;
        BEGIN
            SELECT index_row.indisunique,
                   table_row.relname,
                   array_agg(attribute.attname ORDER BY key_row.ordinality),
                   index_row.indpred::text,
                   index_row.indexprs::text
              INTO existing_unique,
                   existing_table,
                   existing_columns,
                   existing_predicate,
                   existing_expressions
              FROM pg_class AS index_relation
              JOIN pg_namespace AS namespace
                ON namespace.oid = index_relation.relnamespace
              JOIN pg_index AS index_row
                ON index_row.indexrelid = index_relation.oid
              JOIN pg_class AS table_row
                ON table_row.oid = index_row.indrelid
              LEFT JOIN LATERAL unnest(index_row.indkey)
                WITH ORDINALITY AS key_row(attnum, ordinality) ON true
              LEFT JOIN pg_attribute AS attribute
                ON attribute.attrelid = table_row.oid
               AND attribute.attnum = key_row.attnum
             WHERE namespace.nspname = current_schema()
               AND index_relation.relname =
                   'uq_reminder_occurrences_template_scheduled_at'
             GROUP BY index_row.indisunique,
                      table_row.relname,
                      index_row.indpred::text,
                      index_row.indexprs::text;

            IF NOT FOUND THEN
                IF EXISTS (
                    SELECT 1
                      FROM reminder_occurrences
                     GROUP BY reminder_template_id, scheduled_at
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'cannot create reminder occurrence uniqueness index: duplicate template/scheduled_at rows exist';
                END IF;

                CREATE UNIQUE INDEX
                    uq_reminder_occurrences_template_scheduled_at
                    ON reminder_occurrences
                    (reminder_template_id, scheduled_at);
            ELSIF NOT existing_unique
               OR existing_table <> 'reminder_occurrences'
               OR existing_columns <> ARRAY[
                    'reminder_template_id',
                    'scheduled_at'
                  ]::text[]
               OR existing_predicate IS NOT NULL
               OR existing_expressions IS NOT NULL THEN
                RAISE EXCEPTION
                    'uq_reminder_occurrences_template_scheduled_at has incompatible definition';
            END IF;
        END
        $reconcile_occurrence_index$;
        """
    )


def downgrade() -> None:
    # Downgrading deliberately removes the scheduling contract even when the
    # upgrade adopted objects that were already present.
    op.execute(
        "DROP INDEX IF EXISTS "
        "uq_reminder_occurrences_template_scheduled_at"
    )
    op.execute(
        "ALTER TABLE reminder_templates DROP CONSTRAINT IF EXISTS "
        "reminder_due_after_nonnegative"
    )
    op.execute(
        "ALTER TABLE reminder_templates DROP COLUMN IF EXISTS "
        "due_after_minutes"
    )
    op.execute(
        "ALTER TABLE reminder_templates DROP COLUMN IF EXISTS timezone"
    )
