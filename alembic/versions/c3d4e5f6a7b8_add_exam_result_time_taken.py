"""add time_taken to exam_results

Elapsed seconds a student spent on an attempt, sent by the frontend on submit.
Null for older attempts.

NOTE: down_revision targets the LOCAL alembic head chain (b2c3d4e5f6a7). Local and
VPS histories are deliberately divergent (see CLAUDE.md); rebase down_revision onto
the VPS head before deploying there.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-03 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('exam_results', sa.Column('time_taken', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('exam_results', 'time_taken')
