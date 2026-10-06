"""add users.is_top_performer

Boolean flag set daily by app.jobs.top_performers for users currently in the Top 10
of any full-test leaderboard (Top Performer badge).

NOTE: revision id chosen to avoid the a7b8c9d0e1f2 collision (already used by
add_result_answer_snapshots). down_revision = local head f6a7b8c9d0e1.

Revision ID: d1e2f3a4b5c6
Revises: f6a7b8c9d0e1
Create Date: 2026-08-05 22:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('is_top_performer', sa.Boolean(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('users', 'is_top_performer')
