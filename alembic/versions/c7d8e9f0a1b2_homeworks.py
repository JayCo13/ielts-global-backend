"""homeworks (center teacher assignments)

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-07-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'homeworks',
        sa.Column('homework_id', sa.Integer(), primary_key=True),
        sa.Column('center_id', sa.Integer(), sa.ForeignKey('centers.center_id'), nullable=False, index=True),
        sa.Column('class_id', sa.Integer(), sa.ForeignKey('classrooms.class_id'), nullable=False, index=True),
        sa.Column('teacher_id', sa.Integer(), sa.ForeignKey('users.user_id'), nullable=True),
        sa.Column('exam_id', sa.Integer(), sa.ForeignKey('exams.exam_id'), nullable=False),
        sa.Column('skill', sa.String(length=20), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('due_date', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('homeworks')
