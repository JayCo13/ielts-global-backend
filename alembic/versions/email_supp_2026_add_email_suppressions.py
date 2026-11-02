"""Add email_suppressions (marketing unsubscribes, bounces, complaints).

Revision ID: email_supp_2026
Revises: f2b1e0a7c3d9
Create Date: 2026-11-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'email_supp_2026'
down_revision: Union[str, Sequence[str], None] = 'f2b1e0a7c3d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'email_suppressions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_email_suppressions_email', 'email_suppressions', ['email'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_email_suppressions_email', table_name='email_suppressions')
    op.drop_table('email_suppressions')
