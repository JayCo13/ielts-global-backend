"""add ai_score_usage table (server-side AI Writing quota / fair-usage)

Revision ID: ai_usage_2026
Revises: vocab_ws_2026
Create Date: 2026-08-08 09:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ai_usage_2026'
down_revision: Union[str, None] = 'vocab_ws_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_score_usage',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(),
                  sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False, server_default='grade'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_ai_score_usage_user_id', 'ai_score_usage', ['user_id'])
    op.create_index('ix_ai_score_usage_created_at', 'ai_score_usage', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_ai_score_usage_created_at', table_name='ai_score_usage')
    op.drop_index('ix_ai_score_usage_user_id', table_name='ai_score_usage')
    op.drop_table('ai_score_usage')
