"""Speaking: độ khó chủ đề, cùng thiết kế với 3 kỹ năng kia (feedback 07/09).

Revision ID: spk10_2026
Revises: spk9_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'spk10_2026'
down_revision = 'spk9_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('speaking_topics', sa.Column('difficulty_score', sa.Float(), nullable=True))
    op.add_column('speaking_topics', sa.Column('difficulty_label', sa.String(length=16), nullable=True))
    op.add_column('speaking_topics', sa.Column('difficulty_valid_count', sa.Integer(),
                                               nullable=False, server_default='0'))
    op.add_column('speaking_topics', sa.Column('difficulty_updated_at', sa.DateTime(), nullable=True))


def downgrade():
    for c in ('difficulty_updated_at', 'difficulty_valid_count',
              'difficulty_label', 'difficulty_score'):
        op.drop_column('speaking_topics', c)
