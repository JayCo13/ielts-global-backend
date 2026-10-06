"""add per-skill Hall of Fame columns (read/listen/write _top10_count/_hof_attempts/_hof_score)

Hall of Fame is split per skill (Listening/Reading/Writing). Each skill has its own
Top-10 count + cumulative attempts + score, computed daily by app.jobs.top_performers.

NOTE: down_revision = local head e2f3a4b5c6d7. Rebase onto the VPS head if it
differs (see CLAUDE.md).

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-06 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [
    'read_top10_count', 'read_hof_attempts', 'read_hof_score',
    'listen_top10_count', 'listen_hof_attempts', 'listen_hof_score',
    'write_top10_count', 'write_hof_attempts', 'write_hof_score',
]


def upgrade() -> None:
    for c in _COLS:
        op.add_column('users', sa.Column(c, sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    for c in reversed(_COLS):
        op.drop_column('users', c)
