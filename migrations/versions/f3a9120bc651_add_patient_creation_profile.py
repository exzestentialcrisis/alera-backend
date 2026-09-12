"""Support optional demographics and caregiver-entered patient profiles.

Revision ID: f3a9120bc651
Revises: e2c7f19a6b40
"""
from alembic import op
import sqlalchemy as sa

revision = "f3a9120bc651"
down_revision = "e2c7f19a6b40"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("elderly_patients", "birthdate", nullable=True)
    op.alter_column("elderly_patients", "sex", nullable=True)
    for name, kind in (
        ("address_or_room", sa.Text()),
        ("emergency_contact_name", sa.String(150)),
        ("emergency_contact_phone", sa.String(30)),
        ("baseline_heart_rate", sa.Numeric(6, 2)),
        ("baseline_spo2", sa.Numeric(5, 2)),
    ):
        op.add_column("elderly_patients", sa.Column(name, kind, nullable=True))


def downgrade():
    # Refuse to invent demographics or silently discard newly entered profiles.
    count = op.get_bind().scalar(sa.text("""
        SELECT count(*) FROM elderly_patients
        WHERE birthdate IS NULL OR sex IS NULL OR address_or_room IS NOT NULL
           OR emergency_contact_name IS NOT NULL OR emergency_contact_phone IS NOT NULL
           OR baseline_heart_rate IS NOT NULL OR baseline_spo2 IS NOT NULL
    """))
    if count:
        raise RuntimeError("Patient profiles must be preserved externally before downgrade.")
    for name in (
        "baseline_spo2", "baseline_heart_rate", "emergency_contact_phone",
        "emergency_contact_name", "address_or_room",
    ):
        op.drop_column("elderly_patients", name)
    op.alter_column("elderly_patients", "sex", nullable=False)
    op.alter_column("elderly_patients", "birthdate", nullable=False)
