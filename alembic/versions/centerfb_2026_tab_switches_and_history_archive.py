"""center feedback: tab-switch counts + student history archive

Adds:
- exam_results.tab_switches      (item 9: persisted tab-switch count per attempt)
- exam_progress.tab_switches     (item 9: live tab-switch count on the realtime board)
- student_history_archives table (item 8: archive-then-clear a student's history)

Revision ID: centerfb_2026
Revises: mcup_2026
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB

revision = 'centerfb_2026'
down_revision = 'mcup_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('exam_results', sa.Column('tab_switches', sa.Integer(), nullable=True))
    op.add_column('exam_progress', sa.Column('tab_switches', sa.Integer(), server_default='0', nullable=True))

    op.create_table(
        'student_history_archives',
        sa.Column('archive_id', sa.Integer(), primary_key=True, index=True),
        sa.Column('center_id', sa.Integer(), sa.ForeignKey('centers.center_id'), nullable=True, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id'), nullable=False, index=True),
        sa.Column('username', sa.String(length=50), nullable=True),
        sa.Column('archived_by', sa.Integer(), nullable=True),
        sa.Column('num_exams', sa.Integer(), server_default='0'),
        sa.Column('num_writing', sa.Integer(), server_default='0'),
        sa.Column('data_gz', LONGBLOB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('student_history_archives')
    op.drop_column('exam_progress', 'tab_switches')
    op.drop_column('exam_results', 'tab_switches')
