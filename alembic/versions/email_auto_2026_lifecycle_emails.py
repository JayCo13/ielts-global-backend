"""Lifecycle emails: checkout intents + automated email log.

Revision ID: email_auto_2026
Revises: creator_chan_2026
Create Date: 2026-11-14 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'email_auto_2026'
down_revision: Union[str, Sequence[str], None] = 'creator_chan_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'checkout_intents',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('package_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_checkout_intents_user_id', 'checkout_intents', ['user_id'])
    op.create_index('ix_checkout_intents_created_at', 'checkout_intents', ['created_at'])
    op.create_table(
        'marketing_email_logs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('ref', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='sending'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('user_id', 'kind', 'ref', name='uq_marketing_email_user_kind_ref'),
    )
    op.create_index('ix_marketing_email_logs_user_id', 'marketing_email_logs', ['user_id'])
    op.create_index('ix_marketing_email_logs_created_at', 'marketing_email_logs', ['created_at'])


def downgrade() -> None:
    op.drop_table('marketing_email_logs')
    op.drop_table('checkout_intents')
