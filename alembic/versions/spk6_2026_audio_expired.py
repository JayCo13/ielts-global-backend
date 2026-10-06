"""Speaking: đánh dấu bản ghi đã bị dọn (docs/speaking-spec.md §9).

Ghi âm không giữ được vĩnh viễn, nhưng lịch sử thì có — transcript, 4 điểm tiêu chí và
nhận xét ở lại mãi. Cột này để giao diện phân biệt "chưa từng ghi âm" với "đã ghi rồi
nhưng hết hạn lưu trữ"; thiếu nó thì một câu cũ trông như học viên chưa từng trả lời.

Revision ID: spk6_2026
Revises: spk5_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'spk6_2026'
down_revision = 'spk5_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('speaking_attempt_answers',
                  sa.Column('audio_expired', sa.Boolean(), nullable=False,
                            server_default='0'))


def downgrade():
    op.drop_column('speaking_attempt_answers', 'audio_expired')
