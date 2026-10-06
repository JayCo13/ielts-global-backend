"""add result_answer_snapshots table

Stores a gzipped, self-contained snapshot of each result's reviewable answers
(+ optional highlights/notes) so "xem lại bài" survives future admin edits that
hard-delete the live StudentAnswer/ListeningAnswer rows.

NOTE: down_revision targets the CLOUD alembic head (a1b2c3d4e5f6 = email_verified).
Local and VPS alembic histories are deliberately divergent (see CLAUDE.md); before
deploying, re-check `alembic current` on the VPS and rebase down_revision onto its
actual head if it differs.

Revision ID: a7b8c9d0e1f2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'result_answer_snapshots',
        sa.Column('result_id', sa.Integer(), nullable=False),
        sa.Column('answers_gz', mysql.LONGBLOB(), nullable=True),
        sa.Column('annotations_gz', mysql.LONGBLOB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['result_id'], ['exam_results.result_id'], ),
        sa.PrimaryKeyConstraint('result_id'),
    )


def downgrade() -> None:
    op.drop_table('result_answer_snapshots')
