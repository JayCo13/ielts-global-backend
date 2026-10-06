"""Pronunciation Lessons: Unit lý thuyết + bộ luyện tập do AI sinh (feedback 09/09).

Revision ID: spk11_2026
Revises: spk10_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'spk11_2026'
down_revision = 'spk10_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'speaking_pron_units',
        sa.Column('unit_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('theory', sa.Text(), nullable=True),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_published', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('unit_id'),
    )
    op.create_index(op.f('ix_speaking_pron_units_unit_id'), 'speaking_pron_units',
                    ['unit_id'], unique=False)

    op.create_table(
        'speaking_pron_items',
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('unit_id', sa.Integer(), nullable=False),
        # NULL = bộ mặc định của Unit; có giá trị = bộ riêng của học viên đó.
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.Enum('word', 'sentence', name='speaking_pron_item_kinds'),
                  nullable=False, server_default='word'),
        sa.Column('content', sa.String(length=500), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=True),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['unit_id'], ['speaking_pron_units.unit_id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('item_id'),
    )
    op.create_index(op.f('ix_speaking_pron_items_item_id'), 'speaking_pron_items',
                    ['item_id'], unique=False)
    op.create_index(op.f('ix_speaking_pron_items_unit_id'), 'speaking_pron_items',
                    ['unit_id'], unique=False)
    op.create_index(op.f('ix_speaking_pron_items_user_id'), 'speaking_pron_items',
                    ['user_id'], unique=False)


def downgrade():
    op.drop_table('speaking_pron_items')
    op.drop_table('speaking_pron_units')
