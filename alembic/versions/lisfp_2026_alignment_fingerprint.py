"""Add listening_alignments.source_fingerprint.

Lets the nightly job notice that a part's transcript was edited or its audio replaced,
and re-align it. Without this the job skips anything already aligned, so corrected
transcripts kept the old — now wrong — timestamps.

Local counterpart of the cloud revision `lisfp_prod_2026`.

Revision ID: lisfp_2026
Revises: candict_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'lisfp_2026'
down_revision = 'candict_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('listening_alignments',
                  sa.Column('source_fingerprint', sa.String(length=64), nullable=True))


def downgrade():
    op.drop_column('listening_alignments', 'source_fingerprint')
