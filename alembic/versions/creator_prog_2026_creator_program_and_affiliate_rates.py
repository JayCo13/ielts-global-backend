"""Creator program ("Work With Us") tables + affiliate per-account commission rates.

Seeds the affiliate default-rate history: 20% for every order created before this
migration (unchanged behaviour), 5% for orders created after it.

Revision ID: creator_prog_2026
Revises: email_supp_2026
Create Date: 2026-11-09 00:00:00.000000
"""
from datetime import datetime, timedelta
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'creator_prog_2026'
down_revision: Union[str, Sequence[str], None] = 'email_supp_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'affiliate_rate_changes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=True),
        sa.Column('rate_pct', sa.Float(), nullable=True),
        sa.Column('effective_at', sa.DateTime(), nullable=False),
        sa.Column('changed_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_affiliate_rate_changes_user_id', 'affiliate_rate_changes', ['user_id'])
    op.create_index('ix_affiliate_rate_changes_effective_at', 'affiliate_rate_changes', ['effective_at'])
    now_vn = datetime.utcnow() + timedelta(hours=7)   # Vietnam-naive, like every other column
    rates = sa.table('affiliate_rate_changes',
                     sa.column('user_id', sa.Integer), sa.column('rate_pct', sa.Float),
                     sa.column('effective_at', sa.DateTime), sa.column('created_at', sa.DateTime))
    op.bulk_insert(rates, [
        {'user_id': None, 'rate_pct': 20.0, 'effective_at': datetime(2000, 1, 1), 'created_at': now_vn},
        {'user_id': None, 'rate_pct': 5.0, 'effective_at': now_vn, 'created_at': now_vn},
    ])

    op.create_table(
        'creator_applications',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='SET NULL'), nullable=True),
        sa.Column('full_name', sa.String(length=120), nullable=False),
        sa.Column('country_code', sa.String(length=8), nullable=True),
        sa.Column('languages', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=40), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='new'),
        sa.Column('admin_note', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_creator_applications_user_id', 'creator_applications', ['user_id'])
    op.create_index('ix_creator_applications_status', 'creator_applications', ['status'])
    op.create_index('ix_creator_applications_created_at', 'creator_applications', ['created_at'])

    op.create_table(
        'creator_showcase_videos',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('video_url', sa.String(length=500), nullable=False),
        sa.Column('platform', sa.String(length=20), nullable=True),
        sa.Column('thumbnail_url', sa.String(length=500), nullable=True),
        sa.Column('handle', sa.String(length=100), nullable=True),
        sa.Column('likes', sa.String(length=20), nullable=True),
        sa.Column('views', sa.String(length=20), nullable=True),
        sa.Column('earned', sa.String(length=20), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )

    op.create_table(
        'creator_testimonials',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('avatar_url', sa.String(length=500), nullable=True),
        sa.Column('subtitle', sa.String(length=200), nullable=True),
        sa.Column('earned', sa.String(length=20), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('profile_url', sa.String(length=500), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('creator_testimonials')
    op.drop_table('creator_showcase_videos')
    op.drop_index('ix_creator_applications_created_at', table_name='creator_applications')
    op.drop_index('ix_creator_applications_status', table_name='creator_applications')
    op.drop_index('ix_creator_applications_user_id', table_name='creator_applications')
    op.drop_table('creator_applications')
    op.drop_index('ix_affiliate_rate_changes_effective_at', table_name='affiliate_rate_changes')
    op.drop_index('ix_affiliate_rate_changes_user_id', table_name='affiliate_rate_changes')
    op.drop_table('affiliate_rate_changes')
