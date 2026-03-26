"""“solve_part_title”

Revision ID: accf7c73e8f7
Revises: add_student_important_words
Create Date: 2026-03-09 09:05:58.165298

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = 'accf7c73e8f7'
down_revision: Union[str, None] = 'add_student_important_words'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('exam_sections', 'part_title',
               existing_type=mysql.VARCHAR(length=500),
               type_=mysql.LONGTEXT(),
               existing_nullable=True)


def downgrade() -> None:
    op.alter_column('exam_sections', 'part_title',
               existing_type=mysql.LONGTEXT(),
               type_=mysql.VARCHAR(length=500),
               existing_nullable=True)

