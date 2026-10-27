"""add item status flags (hidden / popular / new / recommended)

기존에는 display_order == 0 을 '인기' 메뉴로 표시했다. 이를 is_popular로 옮긴다.

Revision ID: 7d2e4f1a9b30
Revises: 3b76e90bf8d1
Create Date: 2026-10-27 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d2e4f1a9b30'
down_revision: Union[str, Sequence[str], None] = '3b76e90bf8d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FLAGS = ('is_hidden', 'is_popular', 'is_new', 'is_recommended')


def upgrade() -> None:
    """Upgrade schema."""
    for name in _FLAGS:
        op.add_column('items', sa.Column(name, sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE items SET is_popular = true WHERE display_order = 0")


def downgrade() -> None:
    """Downgrade schema."""
    for name in reversed(_FLAGS):
        op.drop_column('items', name)
