"""Study Session: task giao được bài Speaking (chủ đề + Part)

`study_session_tasks` trước đây chỉ lưu `exam_id`, nên tab Speaking trong ô tạo buổi học
phải ẩn đi — bày ra chỉ tạo được task rỗng. Thêm hai cột để task trỏ được tới chủ đề
Forecast, giống hệt cách bảng `homeworks` đã làm.

LƯU Ý: prod đi nhánh alembic riêng; phải sinh revision riêng trên VPS khi deploy.

Revision ID: sstaskspk_2026
Revises: sspha2_2026
Create Date: 2026-10-04
"""
from alembic import op
import sqlalchemy as sa

revision = 'sstaskspk_2026'
down_revision = 'sspha2_2026'
branch_labels = None
depends_on = None


def upgrade():
    # Global port: offline (--sql) mode has no connection to inspect; the columns
    # cannot pre-exist on the global DB, so add them unconditionally there.
    from alembic import context
    if context.is_offline_mode():
        cols = set()
    else:
        cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('study_session_tasks')}
    if 'speaking_topic_id' not in cols:
        op.add_column('study_session_tasks', sa.Column('speaking_topic_id', sa.Integer(), nullable=True))
    if 'speaking_section' not in cols:
        op.add_column('study_session_tasks', sa.Column('speaking_section', sa.String(length=20), nullable=True))


def downgrade():
    op.drop_column('study_session_tasks', 'speaking_section')
    op.drop_column('study_session_tasks', 'speaking_topic_id')
