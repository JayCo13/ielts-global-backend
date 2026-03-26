"""add_speaking_material_access_types

Revision ID: 59009b85c58e
Revises: cab02a134f18
Create Date: 2026-01-27 13:51:46.020992

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59009b85c58e'
down_revision: Union[str, None] = 'cab02a134f18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create speaking_material_access_types table
    # Reuse existing 'access_types' enum from ExamAccessType
    op.create_table('speaking_material_access_types',
        sa.Column('material_id', sa.Integer(), nullable=False),
        sa.Column('access_type', sa.Enum('no vip', 'vip', 'student', name='access_types', create_type=False), nullable=False),
        sa.ForeignKeyConstraint(['material_id'], ['speaking_materials.material_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('material_id', 'access_type')
    )


def downgrade() -> None:
    op.drop_table('speaking_material_access_types')

