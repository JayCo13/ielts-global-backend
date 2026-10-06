"""Add listening_cue_overrides — admin-set audio timestamps per question.

Multiple-choice answers are never spoken, so no automatic anchor exists for them; these
rows are set by ear and must survive every re-alignment.

Local counterpart of the cloud revision `liscue_prod_2026`.

Revision ID: liscue_2026
Revises: lisfp_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'liscue_2026'
down_revision = 'lisfp_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'listening_cue_overrides',
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('section_id', sa.Integer(), nullable=True),
        sa.Column('start_time', sa.Float(), nullable=False),
        sa.Column('end_time', sa.Float(), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['question_id'], ['questions.question_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['section_id'], ['exam_sections.section_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.user_id']),
        sa.PrimaryKeyConstraint('question_id'),
    )
    op.create_index('ix_listening_cue_overrides_section_id', 'listening_cue_overrides', ['section_id'])


def downgrade():
    op.drop_index('ix_listening_cue_overrides_section_id', table_name='listening_cue_overrides')
    op.drop_table('listening_cue_overrides')
