"""Study Session Phase 2: dòng thời gian buổi học + điểm danh

Thêm hai bảng:
  - study_session_events      → "Session Replay" (buổi học bắt đầu, task nào chạy/kết thúc/
                                 bị huỷ, buổi học kết thúc)
  - study_session_attendance  → ai có mặt, vào lúc nào, tổng thời gian online

LƯU Ý KHI CHẠY TRÊN PROD: lịch sử alembic của prod là nhánh riêng (head `prodctr_1002` sau
đợt 02/10), còn `down_revision` dưới đây trỏ vào head của máy local. Phải sinh revision
riêng trên VPS hoặc sửa `down_revision` cho khớp — xem phần Migrations trong CLAUDE.md.

Revision ID: sspha2_2026
Revises: hwspk_2026
Create Date: 2026-10-03
"""
from alembic import op
import sqlalchemy as sa

revision = 'sspha2_2026'
down_revision = 'hwspk_2026'
branch_labels = None
depends_on = None


def upgrade():
    # Global port: offline (--sql) mode has no connection to inspect; the tables
    # cannot pre-exist on the global DB, so create them unconditionally there.
    from alembic import context
    if context.is_offline_mode():
        tables = set()
    else:
        tables = set(sa.inspect(op.get_bind()).get_table_names())

    if 'study_session_events' not in tables:
        op.create_table(
            'study_session_events',
            sa.Column('event_id', sa.Integer(), primary_key=True, index=True),
            sa.Column('session_id', sa.Integer(),
                      sa.ForeignKey('study_sessions.session_id'), nullable=False, index=True),
            sa.Column('kind', sa.String(length=24), nullable=False),
            sa.Column('task_id', sa.Integer(), nullable=True),
            sa.Column('task_title', sa.String(length=255), nullable=True),
            sa.Column('note', sa.String(length=255), nullable=True),
            sa.Column('at', sa.DateTime(), nullable=True, index=True),
        )

    if 'study_session_attendance' not in tables:
        op.create_table(
            'study_session_attendance',
            sa.Column('attendance_id', sa.Integer(), primary_key=True, index=True),
            sa.Column('session_id', sa.Integer(),
                      sa.ForeignKey('study_sessions.session_id'), nullable=False, index=True),
            sa.Column('user_id', sa.Integer(),
                      sa.ForeignKey('users.user_id'), nullable=False, index=True),
            sa.Column('joined_at', sa.DateTime(), nullable=True),
            sa.Column('last_seen_at', sa.DateTime(), nullable=True),
            sa.Column('online_seconds', sa.Integer(), server_default='0'),
        )
        # Một học viên chỉ có MỘT dòng điểm danh cho mỗi buổi — chặn ở DB chứ không chỉ
        # dựa vào code, vì nhịp hỏi của nhiều tab cùng lúc rất dễ tạo ra hai dòng.
        op.create_unique_constraint('uq_ss_attendance_session_user',
                                    'study_session_attendance', ['session_id', 'user_id'])


def downgrade():
    op.drop_table('study_session_attendance')
    op.drop_table('study_session_events')
