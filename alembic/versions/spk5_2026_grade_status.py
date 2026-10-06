"""Speaking: trạng thái chấm điểm trên bài thi.

docs/speaking-spec.md §5. Chấm là ba lần gọi AI trên audio nên chạy nền; màn kết quả cần
phân biệt "đang chấm", "xong" và "lỗi" thay vì chỉ thấy điểm rỗng.

Revision ID: spk5_2026
Revises: spk4_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'spk5_2026'
down_revision = 'spk4_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('speaking_attempts',
                  sa.Column('grade_status',
                            sa.Enum('pending', 'running', 'done', 'failed',
                                    name='speaking_grade_status'),
                            server_default='pending', nullable=False))
    op.add_column('speaking_attempts',
                  sa.Column('grade_error', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('speaking_attempts', 'grade_error')
    op.drop_column('speaking_attempts', 'grade_status')
