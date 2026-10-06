"""add time_taken column to writing_answers

Seconds the student spent writing an attempt (shown as "Thời gian đã làm").

Revision ID: wt_time_2026
Revises: wa_gen_2026
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa

revision = 'wt_time_2026'
down_revision = 'wa_gen_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('writing_answers', sa.Column('time_taken', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('writing_answers', 'time_taken')
