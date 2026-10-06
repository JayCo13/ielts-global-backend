"""add auto-forecast (occurrence) columns + system_settings

Occurrence-based auto-forecast: each part (ExamSection / WritingTask) gets an
occurrence_count, forecast_level, forecast_last_updated, skip_first_decay. Plus a
key/value system_settings table for the admin-tunable decay threshold.

NOTE: down_revision = local head d4e5f6a7b8c9. Rebase onto the VPS head if it
differs (see CLAUDE.md).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-04 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ('exam_sections', 'writing_tasks'):
        op.add_column(table, sa.Column('occurrence_count', sa.Integer(), nullable=False, server_default='0'))
        op.add_column(table, sa.Column('forecast_level', sa.Integer(), nullable=True))
        op.add_column(table, sa.Column('forecast_last_updated', sa.DateTime(), nullable=True))
        op.add_column(table, sa.Column('skip_first_decay', sa.Boolean(), nullable=False, server_default='0'))
    op.create_table(
        'system_settings',
        sa.Column('setting_key', sa.String(length=64), nullable=False),
        sa.Column('setting_value', sa.String(length=255), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('setting_key'),
    )


def downgrade() -> None:
    op.drop_table('system_settings')
    for table in ('exam_sections', 'writing_tasks'):
        op.drop_column(table, 'skip_first_decay')
        op.drop_column(table, 'forecast_last_updated')
        op.drop_column(table, 'forecast_level')
        op.drop_column(table, 'occurrence_count')
