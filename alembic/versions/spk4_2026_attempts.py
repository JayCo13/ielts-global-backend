"""Speaking: a student's attempt at a test, and one row per question inside it.

docs/speaking-spec.md §4.6. The assembled paper is frozen into speaking_attempts.plan
rather than rebuilt on demand — the bank keeps moving underneath (admins edit wording,
the decay job deactivates topics) and both "thi lại đúng bộ câu hỏi cũ" and the marker
need to see exactly what was asked.

Answer rows are created for every question when the test starts, so a question the
student stayed silent through still has a row with its own status.

Revision ID: spk4_2026
Revises: spk3_2026
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

revision = 'spk4_2026'
down_revision = 'spk3_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'speaking_attempts',
        sa.Column('attempt_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('test_type', sa.Enum('full', 'part1', 'part2', 'part3',
                                       name='speaking_test_types'), nullable=False),
        sa.Column('mode', sa.Enum('practice', 'mock', name='speaking_modes'), nullable=False),
        sa.Column('input_method', sa.Enum('micro', 'subtitle', name='speaking_input_methods'),
                  server_default='micro', nullable=False),
        sa.Column('occupation', sa.Enum('student', 'working', name='speaking_occupations'),
                  nullable=True),
        sa.Column('voice', sa.String(length=32), nullable=True),
        sa.Column('use_forecast', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('forecast_month', sa.Date(), nullable=True),
        sa.Column('exam_priority', sa.Enum('default', 'done', 'undone',
                                           name='speaking_exam_priority'),
                  server_default='default', nullable=False),
        sa.Column('plan', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('in_progress', 'completed', 'abandoned', 'terminated',
                                    'interrupted', name='speaking_attempt_status'),
                  server_default='in_progress', nullable=False),
        sa.Column('end_reason', sa.String(length=255), nullable=True),
        sa.Column('submitted', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('overall_band', sa.Float(), nullable=True),
        sa.Column('criteria', sa.JSON(), nullable=True),
        sa.Column('part_results', sa.JSON(), nullable=True),
        sa.Column('graded_at', sa.DateTime(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('attempt_id'),
    )
    op.create_index('ix_speaking_attempts_user_id', 'speaking_attempts', ['user_id'])

    op.create_table(
        'speaking_attempt_answers',
        sa.Column('answer_id', sa.Integer(), nullable=False),
        sa.Column('attempt_id', sa.Integer(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=True),
        sa.Column('topic_id', sa.Integer(), nullable=True),
        sa.Column('part', sa.Enum('part1', 'part2', 'part2_followup', 'part3',
                                  name='speaking_answer_parts'), nullable=False),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('topic_title', sa.String(length=255), nullable=True),
        sa.Column('question_text', LONGTEXT(), nullable=True),
        sa.Column('is_ai_followup', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('first_text', LONGTEXT(), nullable=True),
        sa.Column('retry_text', LONGTEXT(), nullable=True),
        sa.Column('first_audio', sa.String(length=255), nullable=True),
        sa.Column('retry_audio', sa.String(length=255), nullable=True),
        sa.Column('used_retry', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('answer_status', sa.Enum('answered', 'no_answer', 'auto_skipped',
                                           'time_expired', name='speaking_answer_status'),
                  server_default='no_answer', nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('scores', sa.JSON(), nullable=True),
        sa.Column('feedback', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        # The bank may be pruned long after a test was taken; the answer and its frozen
        # question text survive that, which is why these null out instead of cascading.
        sa.ForeignKeyConstraint(['attempt_id'], ['speaking_attempts.attempt_id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['question_id'], ['speaking_questions.question_id'],
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['topic_id'], ['speaking_topics.topic_id'],
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('answer_id'),
    )
    op.create_index('ix_speaking_attempt_answers_attempt_id', 'speaking_attempt_answers',
                    ['attempt_id'])
    op.create_index('ix_speaking_attempt_answers_question_id', 'speaking_attempt_answers',
                    ['question_id'])
    op.create_index('ix_speaking_attempt_answers_topic_id', 'speaking_attempt_answers',
                    ['topic_id'])
    op.create_index('ix_speaking_attempt_answers_part', 'speaking_attempt_answers', ['part'])


def downgrade():
    op.drop_table('speaking_attempt_answers')
    op.drop_table('speaking_attempts')
