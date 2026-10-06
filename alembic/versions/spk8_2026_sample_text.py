"""Speaking: giữ câu mẫu do AI viết (feedback 06/09).

§6.3 chỉ tính câu mẫu là một lần học viên TỰ NÓI. Feedback muốn giữ được cả câu AI tạo ra
từ ý tưởng của mình làm bản tham chiếu, nên cần chỗ lưu riêng — và nhãn phải ghi rõ đó là
câu AI, không phải bài nói của học viên.

Revision ID: spk8_2026
Revises: spk7_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'spk8_2026'
down_revision = 'spk7_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('speaking_question_progress', sa.Column('sample_text', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('speaking_question_progress', 'sample_text')
