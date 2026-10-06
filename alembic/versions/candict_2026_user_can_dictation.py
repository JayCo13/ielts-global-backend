"""Add users.can_dictation — per-account grant for the Offline Class section.

Local counterpart of the cloud revision `candict_prod_2026` (kept in
alembic/versions_prod/). Same column, different parent, because the two histories are
deliberately divergent — see CLAUDE.md. Without it the local DB can't even load a user:
models.py declares the column, so every query on `users` fails with "Unknown column".

Revision ID: candict_2026
Revises: lisalign_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'candict_2026'
down_revision = 'lisalign_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'users',
        sa.Column('can_dictation', sa.Boolean(), nullable=False, server_default='0'),
    )


def downgrade():
    op.drop_column('users', 'can_dictation')
