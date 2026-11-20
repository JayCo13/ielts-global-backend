"""Creator bonus policy: per-channel monthly tier bonus, video links, monthly views cap.

Revision ID: creator_bonus_2026
Revises: aff_disc_2026
Create Date: 2026-11-19 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'creator_bonus_2026'
down_revision: Union[str, Sequence[str], None] = 'aff_disc_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col in ('views_reward_raw_usd', 'views_reward_usd', 'tier_bonus_usd', 'video_reward_usd'):
        op.add_column('creator_reconciliations', sa.Column(col, sa.Float(), nullable=True))
    op.add_column('creator_reconciliation_items', sa.Column('tier_views', sa.BigInteger(), nullable=True))
    op.add_column('creator_reconciliation_items', sa.Column('tier_bonus_usd', sa.Float(), nullable=True))
    op.create_table(
        'creator_video_links',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('reconciliation_id', sa.Integer(),
                  sa.ForeignKey('creator_reconciliations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('url', sa.String(500), nullable=False),
        sa.Column('url_key', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('reward_usd', sa.Float(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_creator_video_links_user_id', 'creator_video_links', ['user_id'])
    op.create_index('ix_creator_video_links_reconciliation_id', 'creator_video_links', ['reconciliation_id'])
    op.create_index('ix_creator_video_links_status', 'creator_video_links', ['status'])
    op.create_index('uq_creator_video_links_url_key', 'creator_video_links', ['url_key'], unique=True)


def downgrade() -> None:
    op.drop_table('creator_video_links')
    op.drop_column('creator_reconciliation_items', 'tier_bonus_usd')
    op.drop_column('creator_reconciliation_items', 'tier_views')
    for col in ('video_reward_usd', 'tier_bonus_usd', 'views_reward_usd', 'views_reward_raw_usd'):
        op.drop_column('creator_reconciliations', col)
