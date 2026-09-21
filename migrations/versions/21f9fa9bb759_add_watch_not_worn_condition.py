"""add watch not worn condition

Revision ID: 21f9fa9bb759
Revises: 8b27637d369b
Create Date: 2026-09-21 19:22:13.616919

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '21f9fa9bb759'
down_revision: Union[str, Sequence[str], None] = '8b27637d369b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    
    op.execute(
        "ALTER TYPE condition_key "
        "ADD VALUE IF NOT EXISTS 'WATCH_NOT_WORN'"
    )



def downgrade() -> None:
    """Downgrade schema."""
    pass
