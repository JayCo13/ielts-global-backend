"""Speaking: đếm lượt dùng theo ngày (AI Shadowing, feedback 06/09).

Đặt trong DB chứ không đếm bằng Redis — `redis_cache` trả rỗng khi Redis chết, nên hạn mức
đặt ở đó sẽ tự mở toang đúng lúc hạ tầng có chuyện.

Revision ID: spk9_2026
Revises: spk8_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'spk9_2026'
down_revision = 'spk8_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'speaking_daily_usage',
        sa.Column('usage_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('feature', sa.String(length=32), nullable=False),
        sa.Column('used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('usage_id'),
        sa.UniqueConstraint('user_id', 'day', 'feature',
                            name='uq_speaking_usage_user_day_feature'),
    )
    op.create_index('ix_speaking_daily_usage_usage_id', 'speaking_daily_usage', ['usage_id'])
    op.create_index('ix_speaking_daily_usage_user_id', 'speaking_daily_usage', ['user_id'])
    op.create_index('ix_speaking_daily_usage_day', 'speaking_daily_usage', ['day'])


def downgrade():
    op.drop_table('speaking_daily_usage')
