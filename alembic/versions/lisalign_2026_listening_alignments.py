"""Add listening_alignments — word-level audio timing per listening part.

Local counterpart of the cloud revision `lisalign_prod_2026` (kept in
alembic/versions_prod/). Same table, different parent, because the two histories are
deliberately divergent — see CLAUDE.md.

Revision ID: lisalign_2026
Revises: coursedays_2026
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB

revision = 'lisalign_2026'
down_revision = 'coursedays_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'listening_alignments',
        sa.Column('section_id', sa.Integer(), nullable=False),
        sa.Column('audio_duration', sa.Float(), nullable=True),
        sa.Column('token_count', sa.Integer(), nullable=True),
        sa.Column('aligned_count', sa.Integer(), nullable=True),
        sa.Column('coverage_pct', sa.Float(), nullable=True),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('data_gz', LONGBLOB(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['section_id'], ['exam_sections.section_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('section_id'),
    )


def downgrade():
    op.drop_table('listening_alignments')
