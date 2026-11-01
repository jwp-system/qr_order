"""contacts (회사 문의 폼 /contact)

Revision ID: f4c8a1d3e902
Revises: e9a2b7c4d618
Create Date: 2026-11-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4c8a1d3e902'
down_revision: Union[str, Sequence[str], None] = 'e9a2b7c4d618'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'contacts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('topic', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=30), nullable=False),
        sa.Column('company', sa.String(length=100), nullable=True),
        sa.Column('email', sa.String(length=100), nullable=False),
        sa.Column('phone', sa.String(length=30), nullable=True),
        sa.Column('scale', sa.String(length=100), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('memo', sa.Text(), nullable=True),
        sa.Column('is_done', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_contacts_topic'), 'contacts', ['topic'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_contacts_topic'), table_name='contacts')
    op.drop_table('contacts')
