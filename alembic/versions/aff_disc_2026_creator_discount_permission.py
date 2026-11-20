"""Affiliate discount permission per creator.

Revision ID: aff_disc_2026
Revises: email_auto_2026
Create Date: 2026-11-14 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'aff_disc_2026'
down_revision: Union[str, Sequence[str], None] = 'email_auto_2026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('creator_profiles', sa.Column('discount_enabled', sa.Boolean(), nullable=False,
                                                server_default=sa.text('0')))


def downgrade() -> None:
    op.drop_column('creator_profiles', 'discount_enabled')
