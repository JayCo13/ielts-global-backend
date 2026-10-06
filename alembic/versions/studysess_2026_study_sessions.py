"""study session (Phase 1): study_sessions + study_session_tasks

Revision ID: studysess_2026
Revises: centerfb_2026
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = 'studysess_2026'
down_revision = 'centerfb_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'study_sessions',
        sa.Column('session_id', sa.Integer(), primary_key=True, index=True),
        sa.Column('center_id', sa.Integer(), sa.ForeignKey('centers.center_id'), nullable=True, index=True),
        sa.Column('class_id', sa.Integer(), sa.ForeignKey('classrooms.class_id'), nullable=False, index=True),
        sa.Column('teacher_id', sa.Integer(), sa.ForeignKey('users.user_id'), nullable=True),
        sa.Column('teacher_name', sa.String(length=50), nullable=True),
        sa.Column('meet_url', sa.String(length=500), nullable=True),
        sa.Column('status', sa.String(length=20), server_default='active', index=True),
        sa.Column('current_task_id', sa.Integer(), nullable=True),
        sa.Column('scheduled_start', sa.DateTime(), nullable=True),
        sa.Column('scheduled_end', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'study_session_tasks',
        sa.Column('task_id', sa.Integer(), primary_key=True, index=True),
        sa.Column('session_id', sa.Integer(), sa.ForeignKey('study_sessions.session_id'), nullable=False, index=True),
        sa.Column('skill', sa.String(length=20), nullable=True),
        sa.Column('exam_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('parts', sa.JSON(), nullable=True),
        sa.Column('order_index', sa.Integer(), server_default='0'),
        sa.Column('status', sa.String(length=20), server_default='pending'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table('study_session_tasks')
    op.drop_table('study_sessions')
