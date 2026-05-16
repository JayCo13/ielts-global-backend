"""add task1_type to writing_tasks

Revision ID: b7f3a2c8d9e1
Revises: accf7c73e8f7
Create Date: 2026-05-16 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7f3a2c8d9e1'
down_revision: Union[str, None] = 'accf7c73e8f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'writing_tasks',
        sa.Column(
            'task1_type',
            sa.Enum('pie', 'map', 'process', 'table', 'line', 'bar', 'mixed', name='task1_question_types'),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('writing_tasks', 'task1_type')
