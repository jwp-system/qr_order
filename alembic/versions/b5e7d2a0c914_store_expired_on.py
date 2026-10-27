"""stores.expired_on (서비스 종료일, 이날부터 1개월간 조회 전용)

이미 EXPIRED인 매장은 마지막 수정일(한국 날짜)을 종료일로 채운다.

Revision ID: b5e7d2a0c914
Revises: 9a41c3e8d215
Create Date: 2026-10-27 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e7d2a0c914'
down_revision: Union[str, Sequence[str], None] = '9a41c3e8d215'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('stores', sa.Column('expired_on', sa.Date(), nullable=True))
    op.execute(
        "UPDATE stores SET expired_on = (updated_at AT TIME ZONE 'Asia/Seoul')::date "
        "WHERE license_status = 'EXPIRED'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('stores', 'expired_on')
