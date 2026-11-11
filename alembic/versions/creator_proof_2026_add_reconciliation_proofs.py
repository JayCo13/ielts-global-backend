"""Creator Hub: proof of views per reconciliation line.

* creator_reconciliation_items: + proof_urls (TEXT, JSON list of R2 URLs)

Revision ID: creator_proof_2026
Revises: creator_hub_2026
Create Date: 2026-11-10 21:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'creator_proof_2026'
down_revision: Union[str, Sequence[str], None] = 'creator_hub_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('creator_reconciliation_items', sa.Column('proof_urls', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('creator_reconciliation_items', 'proof_urls')
