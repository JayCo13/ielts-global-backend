"""Speaking: tiến độ của học viên với từng câu hỏi (docs/speaking-spec.md §6).

Hai thứ sống lâu hơn một bài thi: câu đã xem phân tích chưa (§6.1) và học viên đã tự chọn
câu mẫu nào (§6.3). Một câu hỏi được trả lời nhiều lần qua nhiều bài, nên không đặt được
trên dòng câu trả lời.

Revision ID: spk7_2026
Revises: spk6_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'spk7_2026'
down_revision = 'spk6_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'speaking_question_progress',
        sa.Column('progress_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('viewed_at', sa.DateTime(), nullable=True),
        sa.Column('sample_answer_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['question_id'], ['speaking_questions.question_id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sample_answer_id'],
                                ['speaking_attempt_answers.answer_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('progress_id'),
        sa.UniqueConstraint('user_id', 'question_id',
                            name='uq_speaking_progress_user_question'),
    )
    op.create_index('ix_speaking_question_progress_progress_id',
                    'speaking_question_progress', ['progress_id'])
    op.create_index('ix_speaking_question_progress_user_id',
                    'speaking_question_progress', ['user_id'])
    op.create_index('ix_speaking_question_progress_question_id',
                    'speaking_question_progress', ['question_id'])


def downgrade():
    op.drop_table('speaking_question_progress')
