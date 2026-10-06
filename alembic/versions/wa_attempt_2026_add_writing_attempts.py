"""add writing_attempts history table

Keeps previous Writing attempts ("Làm lại" creates a new version instead of
deleting the old result).

Revision ID: wa_attempt_2026
Revises: wt_time_2026
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'wa_attempt_2026'
down_revision = 'wt_time_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'writing_attempts',
        sa.Column('attempt_id', sa.Integer(), primary_key=True),
        sa.Column('test_id', sa.Integer(), index=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('writing_tasks.task_id'), index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id'), index=True),
        sa.Column('part_number', sa.Integer()),
        sa.Column('attempt_number', sa.Integer()),
        sa.Column('answer_text', mysql.LONGTEXT(), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('task_achievement_score', sa.Float(), nullable=True),
        sa.Column('coherence_cohesion_score', sa.Float(), nullable=True),
        sa.Column('lexical_resource_score', sa.Float(), nullable=True),
        sa.Column('grammatical_range_score', sa.Float(), nullable=True),
        sa.Column('is_ai_evaluated', sa.Boolean(), server_default='0'),
        sa.Column('result', mysql.JSON(), nullable=True),
        sa.Column('ai_generated', mysql.JSON(), nullable=True),
        sa.Column('time_taken', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('writing_attempts')
