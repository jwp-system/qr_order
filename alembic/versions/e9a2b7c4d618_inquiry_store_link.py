"""inquiries.store_id / answer_seen_at (매장 관리자 화면의 사용 중 문의)

Revision ID: e9a2b7c4d618
Revises: d3f81c6a2b47
Create Date: 2026-10-27 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9a2b7c4d618'
down_revision: Union[str, Sequence[str], None] = 'd3f81c6a2b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('inquiries', sa.Column('store_id', sa.Integer(), nullable=True))
    op.add_column('inquiries', sa.Column('answer_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_inquiries_store_id'), 'inquiries', ['store_id'], unique=False)
    op.create_foreign_key('fk_inquiries_store_id', 'inquiries', 'stores', ['store_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_inquiries_store_id', 'inquiries', type_='foreignkey')
    op.drop_index(op.f('ix_inquiries_store_id'), table_name='inquiries')
    op.drop_column('inquiries', 'answer_seen_at')
    op.drop_column('inquiries', 'store_id')
