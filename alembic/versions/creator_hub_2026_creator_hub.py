"""Creator Hub: access control, channels, views reconciliation, shared wallet.

* creator_applications: + reject_reason, reviewed_at, reviewed_by, source, country_other
* affiliate_wallet_tx.type: + 'creator_reward' (enum extended, existing values unchanged)
* new: creator_profiles, creator_access_logs, creator_channels, creator_channel_logs,
       creator_reconciliations, creator_reconciliation_items

Revision ID: creator_hub_2026
Revises: creator_prog_2026
Create Date: 2026-11-09 12:00:00.000000
"""
from datetime import datetime, timedelta
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'creator_hub_2026'
down_revision: Union[str, Sequence[str], None] = 'creator_prog_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('creator_applications', sa.Column('reject_reason', sa.Text(), nullable=True))
    op.add_column('creator_applications', sa.Column('reviewed_at', sa.DateTime(), nullable=True))
    op.add_column('creator_applications', sa.Column('reviewed_by', sa.Integer(), nullable=True))
    op.add_column('creator_applications', sa.Column('source', sa.String(20), nullable=True))
    op.add_column('creator_applications', sa.Column('country_other', sa.String(100), nullable=True))

    op.execute("ALTER TABLE affiliate_wallet_tx MODIFY COLUMN type "
               "ENUM('commission','withdraw','withdraw_refund','creator_reward') NOT NULL")

    op.create_table(
        'creator_profiles',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), primary_key=True),
        sa.Column('hub_access', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('access_source', sa.String(20), nullable=True),
        sa.Column('full_name', sa.String(120), nullable=True),
        sa.Column('phone', sa.String(40), nullable=True),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('country_code', sa.String(8), nullable=True),
        sa.Column('enabled_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_creator_profiles_hub_access', 'creator_profiles', ['hub_access'])

    op.create_table(
        'creator_access_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('action', sa.String(20), nullable=False),
        sa.Column('source', sa.String(20), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_creator_access_logs_user_id', 'creator_access_logs', ['user_id'])

    op.create_table(
        'creator_channels',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('platform', sa.String(20), nullable=False),
        sa.Column('channel_name', sa.String(255), nullable=False),
        sa.Column('channel_url', sa.String(500), nullable=False),
        sa.Column('url_key', sa.String(500), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('status_reason', sa.Text(), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('approved_total_views', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('last_submitted_total', sa.BigInteger(), nullable=True),
        sa.Column('last_submitted_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_creator_channels_user_id', 'creator_channels', ['user_id'])
    op.create_index('ix_creator_channels_status', 'creator_channels', ['status'])
    op.create_index('ix_creator_channels_url_key', 'creator_channels', ['url_key'], mysql_length=191)

    op.create_table(
        'creator_channel_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('creator_channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('from_status', sa.String(20), nullable=True),
        sa.Column('to_status', sa.String(20), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_creator_channel_logs_channel_id', 'creator_channel_logs', ['channel_id'])

    op.create_table(
        'creator_reconciliations',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='submitted'),
        sa.Column('estimated_usd', sa.Float(), nullable=False, server_default='0'),
        sa.Column('approved_units', sa.BigInteger(), nullable=True),
        sa.Column('reject_reason', sa.Text(), nullable=True),
        sa.Column('wallet_tx_id', sa.Integer(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_by', sa.Integer(), nullable=True),
    )
    op.create_index('ix_creator_reconciliations_user_id', 'creator_reconciliations', ['user_id'])
    op.create_index('ix_creator_reconciliations_status', 'creator_reconciliations', ['status'])
    op.create_index('ix_creator_reconciliations_submitted_at', 'creator_reconciliations', ['submitted_at'])

    op.create_table(
        'creator_reconciliation_items',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('reconciliation_id', sa.Integer(),
                  sa.ForeignKey('creator_reconciliations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_id', sa.Integer(), sa.ForeignKey('creator_channels.id', ondelete='CASCADE'), nullable=False),
        sa.Column('channel_type', sa.String(10), nullable=False),
        sa.Column('previous_total', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('submitted_total', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('period_views', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('views_date', sa.Date(), nullable=True),
        sa.Column('rate_per_k', sa.Float(), nullable=False, server_default='0'),
        sa.Column('estimated_usd', sa.Float(), nullable=False, server_default='0'),
    )
    op.create_index('ix_creator_reconciliation_items_reconciliation_id', 'creator_reconciliation_items', ['reconciliation_id'])
    op.create_index('ix_creator_reconciliation_items_channel_id', 'creator_reconciliation_items', ['channel_id'])


def downgrade() -> None:
    op.drop_table('creator_reconciliation_items')
    op.drop_table('creator_reconciliations')
    op.drop_table('creator_channel_logs')
    op.drop_table('creator_channels')
    op.drop_table('creator_access_logs')
    op.drop_table('creator_profiles')
    # affiliate_wallet_tx keeps the 'creator_reward' enum value: wallet history is never deleted.
    op.drop_column('creator_applications', 'country_other')
    op.drop_column('creator_applications', 'source')
    op.drop_column('creator_applications', 'reviewed_by')
    op.drop_column('creator_applications', 'reviewed_at')
    op.drop_column('creator_applications', 'reject_reason')
