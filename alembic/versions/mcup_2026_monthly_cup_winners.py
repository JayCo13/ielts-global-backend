"""add monthly_cup_winners table

Frozen Top-3 of each month's Cúp tháng (per skill) → aggregated into the new
Hall of Fame.

Revision ID: mcup_2026
Revises: wa_lock_2026
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = 'mcup_2026'
down_revision = 'wa_lock_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'monthly_cup_winners',
        sa.Column('winner_id', sa.Integer(), primary_key=True),
        sa.Column('skill', sa.String(length=20), index=True),
        sa.Column('year', sa.Integer(), index=True),
        sa.Column('month', sa.Integer(), index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id'), index=True),
        sa.Column('rank', sa.Integer()),
        sa.Column('top10_count', sa.Integer(), server_default='0'),
        sa.Column('attempts', sa.Integer(), server_default='0'),
        sa.Column('score', sa.Integer(), server_default='0'),
        sa.Column('time_taken', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('monthly_cup_winners')
