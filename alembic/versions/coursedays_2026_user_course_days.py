"""add users.course_days (per-student course-window length)

Revision ID: coursedays_2026
Revises: studysess_2026
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa

revision = 'coursedays_2026'
down_revision = 'studysess_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('course_days', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('users', 'course_days')
