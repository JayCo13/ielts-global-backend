"""widen saved_vocabulary.source_type enum to include writing + speaking

Lets students save New Words from Writing (and, later, Speaking) surfaces, and the
Vocabulary page split into 4 skills.

NOTE: down_revision = local head f3a4b5c6d7e8. Rebase onto the VPS head if it
differs (see CLAUDE.md).

Revision ID: vocab_ws_2026
Revises: f3a4b5c6d7e8
Create Date: 2026-08-07 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'vocab_ws_2026'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = sa.Enum('listening', 'reading', name='vocab_source_types')
_NEW = sa.Enum('listening', 'reading', 'writing', 'speaking', name='vocab_source_types')


def upgrade() -> None:
    op.alter_column('saved_vocabulary', 'source_type',
                    existing_type=_OLD, type_=_NEW, existing_nullable=False)


def downgrade() -> None:
    op.alter_column('saved_vocabulary', 'source_type',
                    existing_type=_NEW, type_=_OLD, existing_nullable=False)
