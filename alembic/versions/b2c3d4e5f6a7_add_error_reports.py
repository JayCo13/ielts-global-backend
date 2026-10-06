"""add error_reports table

Customer-submitted error reports filed from the exam Review screen. Admins review
the queue and mark each report as viewed (is_viewed).

NOTE: down_revision targets the LOCAL alembic head (a7b8c9d0e1f2). Local and VPS
alembic histories are deliberately divergent (see CLAUDE.md); before deploying,
re-check `alembic current` on the VPS and rebase down_revision onto its actual
head if it differs.

Revision ID: b2c3d4e5f6a7
Revises: a7b8c9d0e1f2
Create Date: 2026-08-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'error_reports',
        sa.Column('report_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('exam_id', sa.Integer(), nullable=True),
        sa.Column('result_id', sa.Integer(), nullable=True),
        sa.Column('skill', sa.String(length=20), nullable=True),
        sa.Column('exam_title', sa.String(length=255), nullable=True),
        sa.Column('error_types', sa.JSON(), nullable=True),
        sa.Column('wrong_answer_questions', sa.String(length=255), nullable=True),
        sa.Column('mis_graded_questions', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_viewed', sa.Boolean(), nullable=True),
        sa.Column('viewed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ),
        sa.ForeignKeyConstraint(['exam_id'], ['exams.exam_id'], ),
        sa.ForeignKeyConstraint(['result_id'], ['exam_results.result_id'], ),
        sa.PrimaryKeyConstraint('report_id'),
    )
    op.create_index(op.f('ix_error_reports_report_id'), 'error_reports', ['report_id'], unique=False)
    op.create_index(op.f('ix_error_reports_user_id'), 'error_reports', ['user_id'], unique=False)
    op.create_index(op.f('ix_error_reports_exam_id'), 'error_reports', ['exam_id'], unique=False)
    op.create_index(op.f('ix_error_reports_is_viewed'), 'error_reports', ['is_viewed'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_error_reports_is_viewed'), table_name='error_reports')
    op.drop_index(op.f('ix_error_reports_exam_id'), table_name='error_reports')
    op.drop_index(op.f('ix_error_reports_user_id'), table_name='error_reports')
    op.drop_index(op.f('ix_error_reports_report_id'), table_name='error_reports')
    op.drop_table('error_reports')
