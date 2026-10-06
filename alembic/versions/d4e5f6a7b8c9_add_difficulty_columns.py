"""add difficulty columns to exam_sections and writing_tasks

Daily-recomputed difficulty classification (see app/jobs/recompute_difficulty.py).

NOTE: down_revision = local head c3d4e5f6a7b8 (= cloud head at time of writing).
Rebase onto the VPS head if it differs (see CLAUDE.md).

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-04 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('exam_sections', 'writing_tasks'):
        op.add_column(table, sa.Column('difficulty_score', sa.Float(), nullable=True))
        op.add_column(table, sa.Column('difficulty_label', sa.String(length=20), nullable=True))
        op.add_column(table, sa.Column('difficulty_valid_count', sa.Integer(), nullable=True))
        op.add_column(table, sa.Column('difficulty_updated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    for table in ('exam_sections', 'writing_tasks'):
        op.drop_column(table, 'difficulty_updated_at')
        op.drop_column(table, 'difficulty_valid_count')
        op.drop_column(table, 'difficulty_label')
        op.drop_column(table, 'difficulty_score')
