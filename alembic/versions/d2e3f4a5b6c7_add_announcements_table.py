"""add_announcements_table

Revision ID: d2e3f4a5b6c7
Revises: c1a2b3d4e5f6
Create Date: 2026-07-23 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1a2b3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'announcements',
        sa.Column('announcement_id', sa.Integer(), nullable=False),
        sa.Column('icon', sa.String(length=16), nullable=True),
        sa.Column('content', sa.String(length=500), nullable=False),
        sa.Column('link', sa.String(length=500), nullable=True),
        sa.Column('is_important', sa.Boolean(), nullable=True),
        sa.Column('display_order', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('announcement_id'),
    )
    op.create_index(op.f('ix_announcements_announcement_id'), 'announcements', ['announcement_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_announcements_announcement_id'), table_name='announcements')
    op.drop_table('announcements')
