"""add questions.stats_category

Separate IELTS category (for stats/difficulty) from question_type (render type), so
the "gán dạng câu hỏi" admin tool no longer overwrites the render type and breaks
the exam-taking UI.

NOTE: down_revision = local head e5f6a7b8c9d0. Rebase onto the VPS head if it
differs (see CLAUDE.md).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-05 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('questions', sa.Column('stats_category', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('questions', 'stats_category')
