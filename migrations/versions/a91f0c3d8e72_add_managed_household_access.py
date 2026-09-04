"""add managed household access foundation

Revision ID: a91f0c3d8e72
Revises: d7b2a1f04c6e
Create Date: 2026-09-04
"""

import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a91f0c3d8e72"
down_revision: Union[str, Sequence[str], None] = "d7b2a1f04c6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def upgrade() -> None:
    op.add_column("households", sa.Column("household_code", sa.String(10)))
    connection = op.get_bind()
    household_ids = connection.execute(
        sa.text("SELECT household_id FROM households ORDER BY household_id FOR UPDATE")
    ).scalars()
    allocated: set[str] = set()
    for household_id in household_ids:
        while (candidate := _code()) in allocated:
            pass
        connection.execute(
            sa.text(
                "UPDATE households SET household_code = :code "
                "WHERE household_id = :household_id"
            ),
            {"code": candidate, "household_id": household_id},
        )
        allocated.add(candidate)
    op.alter_column("households", "household_code", nullable=False)
    op.create_unique_constraint(
        "uq_households_household_code", "households", ["household_code"]
    )
    op.execute("""
        CREATE FUNCTION prevent_household_code_update() RETURNS trigger AS $$
        BEGIN
            IF NEW.household_code IS DISTINCT FROM OLD.household_code THEN
                RAISE EXCEPTION 'household_code is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_households_immutable_code
        BEFORE UPDATE OF household_code ON households
        FOR EACH ROW EXECUTE FUNCTION prevent_household_code_update()
    """)

    op.create_table(
        "caregiver_patient_assignments",
        sa.Column("assignment_id", sa.UUID(), nullable=False),
        sa.Column("caregiver_user_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("assigned_by_user_id", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["caregiver_user_id"], ["users.user_id"]),
        sa.ForeignKeyConstraint(["patient_id"], ["elderly_patients.patient_id"]),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("assignment_id"),
    )
    op.create_index(
        "uq_caregiver_patient_assignments_active",
        "caregiver_patient_assignments",
        ["caregiver_user_id", "patient_id"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )
    op.create_index(
        "ix_caregiver_patient_assignments_patient_active",
        "caregiver_patient_assignments",
        ["patient_id"],
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )
    op.create_table(
        "patient_access_codes",
        sa.Column("access_code_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["patient_id"], ["elderly_patients.patient_id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("access_code_id"),
    )
    op.create_index(
        "ix_patient_access_codes_patient_created",
        "patient_access_codes",
        ["patient_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patient_access_codes_patient_created", table_name="patient_access_codes"
    )
    op.drop_table("patient_access_codes")
    op.drop_index(
        "ix_caregiver_patient_assignments_patient_active",
        table_name="caregiver_patient_assignments",
    )
    op.drop_index(
        "uq_caregiver_patient_assignments_active",
        table_name="caregiver_patient_assignments",
    )
    op.drop_table("caregiver_patient_assignments")
    op.execute("DROP TRIGGER trg_households_immutable_code ON households")
    op.execute("DROP FUNCTION prevent_household_code_update()")
    op.drop_constraint(
        "uq_households_household_code", "households", type_="unique"
    )
    op.drop_column("households", "household_code")
