"""add ai_generated column to writing_answers

Persists on-demand AI content (outline / sample essays / key language) so it
survives edits and reloads ("lưu cứng").

Revision ID: wa_gen_2026
Revises: ai_usage_2026
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'wa_gen_2026'
down_revision = 'ai_usage_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('writing_answers', sa.Column('ai_generated', mysql.JSON(), nullable=True))


def downgrade():
    op.drop_column('writing_answers', 'ai_generated')
