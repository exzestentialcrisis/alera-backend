"""Make patient access codes globally searchable without storing plaintext.

Revision ID: c8d4e52f6b91
Revises: f3a9120bc651
"""

import sqlalchemy as sa
from alembic import op

revision = "c8d4e52f6b91"
down_revision = "f3a9120bc651"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "patient_access_codes",
        sa.Column("access_code_selector", sa.String(length=4), nullable=True),
    )
    op.create_index(
        "ix_patient_access_codes_active_selector",
        "patient_access_codes",
        ["access_code_selector"],
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )
    # Legacy codes cannot supply a selector and use an incompatible format. Retain
    # all audit records while preventing their future use.
    op.execute(
        sa.text(
            "UPDATE patient_access_codes SET revoked_at = now() "
            "WHERE used_at IS NULL AND revoked_at IS NULL"
        )
    )


def downgrade():
    op.drop_index("ix_patient_access_codes_active_selector", table_name="patient_access_codes")
    op.drop_column("patient_access_codes", "access_code_selector")
