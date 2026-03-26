"""add student_important_words table

Revision ID: add_student_important_words
Revises: add_is_recommended_forecasts
Create Date: 2026-02-08

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_student_important_words'
down_revision = 'add_is_recommended_forecasts'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'student_important_words',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.user_id', ondelete='CASCADE'), nullable=False),
        sa.Column('word_id', sa.Integer(), sa.ForeignKey('dictation_words.word_id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        mysql_charset='utf8mb4'
    )
    
    # Create unique constraint to prevent duplicate entries
    op.create_unique_constraint(
        'uq_user_word',
        'student_important_words',
        ['user_id', 'word_id']
    )
    
    # Create indexes for faster lookups
    op.create_index('ix_student_important_words_user_id', 'student_important_words', ['user_id'])
    op.create_index('ix_student_important_words_word_id', 'student_important_words', ['word_id'])


def downgrade():
    op.drop_index('ix_student_important_words_word_id', 'student_important_words')
    op.drop_index('ix_student_important_words_user_id', 'student_important_words')
    op.drop_constraint('uq_user_word', 'student_important_words', type_='unique')
    op.drop_table('student_important_words')
