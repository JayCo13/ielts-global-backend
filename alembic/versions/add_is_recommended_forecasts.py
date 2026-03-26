"""add is_recommended to forecasts

Revision ID: add_is_recommended_forecasts
Revises: 
Create Date: 2026-02-04

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_is_recommended_forecasts'
down_revision = '487dddb1971c'
branch_labels = None
depends_on = None


def upgrade():
    # Add is_recommended to exam_sections
    op.add_column('exam_sections', sa.Column('is_recommended', sa.Boolean(), nullable=True, server_default='0'))
    
    # Add is_recommended to writing_tasks
    op.add_column('writing_tasks', sa.Column('is_recommended', sa.Boolean(), nullable=True, server_default='0'))


def downgrade():
    op.drop_column('exam_sections', 'is_recommended')
    op.drop_column('writing_tasks', 'is_recommended')
