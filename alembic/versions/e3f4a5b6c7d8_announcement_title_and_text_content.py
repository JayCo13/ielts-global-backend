"""announcement title + text content

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-07-23 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('announcements', sa.Column('title', sa.String(length=255), nullable=True))
    # Widen content to hold rich HTML (with embedded images) and allow NULL.
    op.alter_column('announcements', 'content',
                    existing_type=sa.String(length=500),
                    type_=sa.Text(),
                    existing_nullable=False,
                    nullable=True)


def downgrade() -> None:
    op.alter_column('announcements', 'content',
                    existing_type=sa.Text(),
                    type_=sa.String(length=500),
                    existing_nullable=True,
                    nullable=False)
    op.drop_column('announcements', 'title')
