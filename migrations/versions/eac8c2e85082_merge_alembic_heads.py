"""merge alembic heads

Revision ID: eac8c2e85082
Revises: 21f9fa9bb759, 4264198d765d
Create Date: 2026-09-21 22:09:00.154030

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eac8c2e85082'
down_revision: Union[str, Sequence[str], None] = ('21f9fa9bb759', '4264198d765d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
