"""add question_type_tags to exam_sections

Revision ID: add_question_type_tags
Revises: add_is_recommended_forecasts
Create Date: 2026-04-16

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_question_type_tags'
down_revision = 'accf7c73e8f7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('exam_sections', sa.Column('question_type_tags', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('exam_sections', 'question_type_tags')
