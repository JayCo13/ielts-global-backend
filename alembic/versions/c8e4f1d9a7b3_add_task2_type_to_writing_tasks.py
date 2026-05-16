"""add task2_type to writing_tasks

Revision ID: c8e4f1d9a7b3
Revises: b7f3a2c8d9e1
Create Date: 2026-05-16 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8e4f1d9a7b3'
down_revision: Union[str, None] = 'b7f3a2c8d9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'writing_tasks',
        sa.Column(
            'task2_type',
            sa.Enum(
                'agree_disagree',
                'positive_negative',
                'advantages_disadvantages',
                'discussion',
                'solutions_effects',
                'two_part_mixed',
                name='task2_question_types',
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('writing_tasks', 'task2_type')
