"""giao bài tập Speaking: homeworks trỏ được tới speaking_topics

Trước đây `homeworks.exam_id` là NOT NULL và là khoá ngoại tới `exams`. Speaking không
sống trong `exams` (nó ghép bài từ `speaking_topics` + `speaking_questions`), nên không có
cách nào giao bài Speaking — picker luôn trả về danh sách rỗng.

Sau migration này, mỗi dòng homework trỏ tới MỘT trong hai:
  - `exam_id`                                  → Listening / Reading / Writing
  - `speaking_topic_id` + `speaking_section`   → Speaking (section = part1|part2|part3)

LƯU Ý KHI CHẠY TRÊN PROD: lịch sử alembic của prod là một nhánh khác (head
`96de086cf026`), và `down_revision` dưới đây trỏ vào head của máy local. Phải sinh một
revision riêng trên VPS, hoặc sửa `down_revision` cho khớp head thật của prod TRƯỚC khi
chạy — xem phần Migrations trong CLAUDE.md.

Revision ID: hwspk_2026
Revises: spk11_2026
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = 'hwspk_2026'
down_revision = 'spk11_2026'
branch_labels = None
depends_on = None


def upgrade():
    # Nới exam_id thành nullable. Giữ nguyên khoá ngoại: dòng L/R/W vẫn phải trỏ đúng đề.
    op.alter_column('homeworks', 'exam_id',
                    existing_type=sa.Integer(), nullable=True)
    op.add_column('homeworks', sa.Column('speaking_topic_id', sa.Integer(), nullable=True))
    op.add_column('homeworks', sa.Column('speaking_section', sa.String(length=20), nullable=True))
    op.create_foreign_key('fk_homeworks_speaking_topic', 'homeworks',
                          'speaking_topics', ['speaking_topic_id'], ['topic_id'])
    op.create_index('ix_homeworks_speaking_topic_id', 'homeworks', ['speaking_topic_id'])


def downgrade():
    # Dòng Speaking không có exam_id nên không thể quay về NOT NULL khi chúng còn đó —
    # xoá chúng trước, nếu không ALTER sẽ gãy giữa chừng và để lại bảng nửa vời.
    op.execute("DELETE FROM homeworks WHERE exam_id IS NULL")
    op.drop_index('ix_homeworks_speaking_topic_id', table_name='homeworks')
    op.drop_constraint('fk_homeworks_speaking_topic', 'homeworks', type_='foreignkey')
    op.drop_column('homeworks', 'speaking_section')
    op.drop_column('homeworks', 'speaking_topic_id')
    op.alter_column('homeworks', 'exam_id',
                    existing_type=sa.Integer(), nullable=False)
