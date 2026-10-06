"""add locked column to writing_answers

Finalized-on-end-review: a locked graded essay can no longer be AI graded/edited.

Revision ID: wa_lock_2026
Revises: wa_attempt_2026
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = 'wa_lock_2026'
down_revision = 'wa_attempt_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('writing_answers', sa.Column('locked', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('writing_answers', 'locked')
