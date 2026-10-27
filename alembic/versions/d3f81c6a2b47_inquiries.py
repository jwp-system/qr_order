"""inquiries (랜딩 문의 게시판)

Revision ID: d3f81c6a2b47
Revises: b5e7d2a0c914
Create Date: 2026-10-27 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f81c6a2b47'
down_revision: Union[str, Sequence[str], None] = 'b5e7d2a0c914'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

inquiry_category = sa.Enum('INTRO', 'TRIAL', 'SUPPORT', 'FEATURE', 'ETC', name='inquirycategory')


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'inquiries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('category', inquiry_category, nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('author_name', sa.String(length=30), nullable=False),
        sa.Column('store_name', sa.String(length=100), nullable=True),
        sa.Column('contact', sa.String(length=100), nullable=False),
        sa.Column('password_hash', sa.String(length=200), nullable=False),
        sa.Column('is_private', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_inquiries_category'), 'inquiries', ['category'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_inquiries_category'), table_name='inquiries')
    op.drop_table('inquiries')
    inquiry_category.drop(op.get_bind(), checkfirst=True)
