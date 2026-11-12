"""Creator Hub: channel initial views + custom platform name.

* creator_channels: + platform_name (VARCHAR 100, for platform 'other')
* creator_channels: + initial_views (BIGINT NOT NULL DEFAULT 0)

Revision ID: creator_chan_2026
Revises: creator_proof_2026
Create Date: 2026-11-11 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'creator_chan_2026'
down_revision: Union[str, Sequence[str], None] = 'creator_proof_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('creator_channels', sa.Column('platform_name', sa.String(length=100), nullable=True))
    op.add_column('creator_channels', sa.Column('initial_views', sa.BigInteger(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('creator_channels', 'initial_views')
    op.drop_column('creator_channels', 'platform_name')
