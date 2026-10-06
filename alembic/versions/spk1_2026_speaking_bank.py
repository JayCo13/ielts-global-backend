"""Speaking question bank: topics, questions, generated suggestions and vocabulary.

Phase 1 of the Speaking feature (see docs/speaking-spec.md §2–3). Part 3 questions hang
off the Part 2 topic they were entered with — that pairing is what the exam relies on —
so there is no separate Part 3 topic table.

Local counterpart of the cloud revision `spk1_prod_2026`.

Revision ID: spk1_2026
Revises: liscue_2026
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

revision = 'spk1_2026'
down_revision = 'liscue_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'speaking_topics',
        sa.Column('topic_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('part', sa.Enum('part1', 'part2', name='speaking_topic_parts'), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('category', sa.Enum('place', 'people', 'education', 'recreation', 'object',
                                      'others', name='speaking_topic_categories'), nullable=True),
        sa.Column('cue_card', LONGTEXT(), nullable=True),
        sa.Column('work_study', sa.Enum('work', 'study', 'neutral', name='speaking_work_study'),
                  nullable=False, server_default='neutral'),
        sa.Column('is_important', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('forecast_level', sa.Integer(), nullable=True),
        sa.Column('appear_from', sa.Date(), nullable=True),
        sa.Column('appear_to', sa.Date(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('last_updated', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.user_id']),
        sa.PrimaryKeyConstraint('topic_id'),
    )
    op.create_index('ix_speaking_topics_part', 'speaking_topics', ['part'])

    op.create_table(
        'speaking_questions',
        sa.Column('question_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=False),
        sa.Column('part', sa.Enum('part1', 'part2_followup', 'part3',
                                  name='speaking_question_parts'), nullable=False),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('content', LONGTEXT(), nullable=False),
        sa.Column('gen_status', sa.Enum('pending', 'running', 'done', 'failed',
                                        name='speaking_gen_status'),
                  nullable=False, server_default='pending'),
        sa.Column('gen_error', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['topic_id'], ['speaking_topics.topic_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('question_id'),
    )
    op.create_index('ix_speaking_questions_topic_id', 'speaking_questions', ['topic_id'])
    op.create_index('ix_speaking_questions_part', 'speaking_questions', ['part'])

    op.create_table(
        'speaking_suggestions',
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('outline', sa.JSON(), nullable=True),
        sa.Column('samples', sa.JSON(), nullable=True),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['question_id'], ['speaking_questions.question_id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('question_id'),
    )

    op.create_table(
        'speaking_vocabularies',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('band_level', sa.Enum('4.5-5.5', '6.0-6.5', '7.0-7.5', '8.0-9.0',
                                        name='speaking_band_levels'), nullable=False),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('term', sa.String(length=255), nullable=False),
        sa.Column('meaning_vi', sa.String(length=500), nullable=True),
        sa.Column('example', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['question_id'], ['speaking_questions.question_id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_speaking_vocabularies_question_id', 'speaking_vocabularies', ['question_id'])


def downgrade():
    op.drop_table('speaking_vocabularies')
    op.drop_table('speaking_suggestions')
    op.drop_table('speaking_questions')
    op.drop_table('speaking_topics')
