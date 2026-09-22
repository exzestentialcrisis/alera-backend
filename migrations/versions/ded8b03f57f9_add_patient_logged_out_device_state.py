"""add patient logged out device state

Revision ID: ded8b03f57f9
Revises: eac8c2e85082
Create Date: 2026-09-23 00:17:35.331553

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ded8b03f57f9'
down_revision: Union[str, Sequence[str], None] = 'eac8c2e85082'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    op.execute(
        "ALTER TYPE device_connection_status "
        "ADD VALUE IF NOT EXISTS 'LOGGED_OUT'"
    )

    op.execute(
        "ALTER TYPE condition_key "
        "ADD VALUE IF NOT EXISTS 'PATIENT_LOGGED_OUT'"
    )

def downgrade() -> None:
    """Downgrade schema."""
    pass
