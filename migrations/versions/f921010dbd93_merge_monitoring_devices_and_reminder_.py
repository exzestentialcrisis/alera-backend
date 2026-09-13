"""merge monitoring devices and reminder heads

Revision ID: f921010dbd93
Revises: 0af09d4cb0fc, a6e1b3c4d5f6
Create Date: 2026-09-13 12:25:33.624771

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f921010dbd93'
down_revision: Union[str, Sequence[str], None] = ('0af09d4cb0fc', 'a6e1b3c4d5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
