"""Add question_type_tags to WritingTask

Revision ID: fdd7f49aface
Revises: add_question_type_tags
Create Date: 2026-05-05 09:20:11.176804

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fdd7f49aface'
down_revision: Union[str, None] = 'add_question_type_tags'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The autogen produced spurious drop/recreate ops on
    # student_important_words indexes — MySQL rejects them because
    # `word_id` has a live FK to dictation_words.word_id and the index
    # backs that FK. The real intent of this migration is just the
    # writing_tasks.question_type_tags column; the rest was noise.
    op.add_column('writing_tasks', sa.Column('question_type_tags', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('writing_tasks', 'question_type_tags')
