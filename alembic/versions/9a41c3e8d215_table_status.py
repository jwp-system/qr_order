"""tables.is_active -> tables.status (AVAILABLE / PAUSED / DISABLED)

is_active=true 는 AVAILABLE, false 는 DISABLED 로 옮긴다.

Revision ID: 9a41c3e8d215
Revises: 7d2e4f1a9b30
Create Date: 2026-10-27 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a41c3e8d215'
down_revision: Union[str, Sequence[str], None] = '7d2e4f1a9b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

table_status = sa.Enum('AVAILABLE', 'PAUSED', 'DISABLED', name='tablestatus')


def upgrade() -> None:
    """Upgrade schema."""
    table_status.create(op.get_bind(), checkfirst=True)
    op.add_column('tables', sa.Column('status', table_status, server_default='AVAILABLE', nullable=False))
    op.execute("UPDATE tables SET status = 'DISABLED' WHERE is_active = false")
    op.drop_column('tables', 'is_active')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('tables', sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False))
    op.execute("UPDATE tables SET is_active = false WHERE status = 'DISABLED'")
    op.drop_column('tables', 'status')
    table_status.drop(op.get_bind(), checkfirst=True)
