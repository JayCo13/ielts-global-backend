"""Add email_broadcasts (admin broadcast email history).

Revision ID: f2b1e0a7c3d9
Revises: f1a0c0de2026
Create Date: 2026-10-06 00:00:00.000000

Ported from the Vietnam tree, where this table was created by hand on the VPS
and never had a migration. Matches the EmailBroadcast model.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = 'f2b1e0a7c3d9'
down_revision: Union[str, Sequence[str], None] = 'f1a0c0de2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_broadcasts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('subject', sa.String(length=500), nullable=False),
        sa.Column('body_html', mysql.LONGTEXT(), nullable=False),
        sa.Column('status', sa.Enum('pending', 'sending', 'completed', 'failed', name='broadcast_status'),
                  nullable=True),
        sa.Column('target_filter', sa.String(length=50), nullable=True),
        sa.Column('total_recipients', sa.Integer(), nullable=True),
        sa.Column('sent_count', sa.Integer(), nullable=True),
        sa.Column('failed_count', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.user_id'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('email_broadcasts')
