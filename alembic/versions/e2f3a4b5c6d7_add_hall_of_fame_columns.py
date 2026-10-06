"""add users.top10_count / hof_attempts / hof_score (Hall of Fame + tiered badges)

Computed daily by app.jobs.top_performers. top10_count drives the badge tier
(1 Top / 3 Elite / 10 Master / 25 Legend); hof_attempts + hof_score are the
permanent Hall of Fame totals.

NOTE: down_revision = local head d1e2f3a4b5c6. Rebase onto the VPS head if it
differs (see CLAUDE.md).

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('top10_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('hof_attempts', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('hof_score', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('users', 'hof_score')
    op.drop_column('users', 'hof_attempts')
    op.drop_column('users', 'top10_count')
