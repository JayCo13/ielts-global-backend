"""center management foundations

Adds the Center (Trung tâm) 3-level org: centers, classrooms, memberships,
class members, wallet transactions, chat messages, exam progress; and extends
the users.role enum with 'center' and 'teacher'.

Revision ID: c1a2b3d4e5f6
Revises: b1f4c2d9a3e7
Create Date: 2026-07-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: Union[str, None] = 'b1f4c2d9a3e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extend the users.role enum (MySQL in-place modify; existing rows unaffected).
    op.execute(
        "ALTER TABLE users MODIFY COLUMN role "
        "ENUM('admin','student','customer','center','teacher')"
    )

    op.create_table(
        'centers',
        sa.Column('center_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('logo_url', sa.String(length=255), nullable=True),
        sa.Column('wallet_balance', sa.Float(), nullable=False, server_default='0'),
        sa.Column('wallet_deposited', sa.Float(), nullable=False, server_default='0'),
        sa.Column('wallet_used', sa.Float(), nullable=False, server_default='0'),
        sa.Column('vip_purchase_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('discount_rate', sa.Float(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id']),
        sa.PrimaryKeyConstraint('center_id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index(op.f('ix_centers_center_id'), 'centers', ['center_id'], unique=False)

    op.create_table(
        'classrooms',
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('center_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['center_id'], ['centers.center_id']),
        sa.PrimaryKeyConstraint('class_id'),
    )
    op.create_index(op.f('ix_classrooms_class_id'), 'classrooms', ['class_id'], unique=False)

    op.create_table(
        'center_memberships',
        sa.Column('membership_id', sa.Integer(), nullable=False),
        sa.Column('center_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('member_type', sa.Enum('teacher', 'student', name='center_member_types'), nullable=False),
        sa.Column('is_paused', sa.Boolean(), nullable=True),
        sa.Column('is_disabled', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['center_id'], ['centers.center_id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id']),
        sa.PrimaryKeyConstraint('membership_id'),
        mysql_charset='utf8mb4',
    )
    op.create_index(op.f('ix_center_memberships_membership_id'), 'center_memberships', ['membership_id'], unique=False)

    op.create_table(
        'class_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['class_id'], ['classrooms.class_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_class_members_id'), 'class_members', ['id'], unique=False)

    op.create_table(
        'center_wallet_transactions',
        sa.Column('transaction_id', sa.Integer(), nullable=False),
        sa.Column('center_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.Enum('deposit', 'vip_purchase', name='center_txn_types'), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('method', sa.String(length=50), nullable=True),
        sa.Column('status', sa.Enum('pending', 'completed', 'reject', name='center_txn_status'), nullable=True),
        sa.Column('target_user_id', sa.Integer(), nullable=True),
        sa.Column('package_id', sa.Integer(), nullable=True),
        sa.Column('discount_rate', sa.Float(), nullable=True),
        sa.Column('payos_order_code', sa.BigInteger(), nullable=True),
        sa.Column('payos_checkout_url', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['center_id'], ['centers.center_id']),
        sa.ForeignKeyConstraint(['target_user_id'], ['users.user_id']),
        sa.ForeignKeyConstraint(['package_id'], ['vip_packages.package_id']),
        sa.PrimaryKeyConstraint('transaction_id'),
    )
    op.create_index(op.f('ix_center_wallet_transactions_transaction_id'), 'center_wallet_transactions', ['transaction_id'], unique=False)
    op.create_index(op.f('ix_center_wallet_transactions_payos_order_code'), 'center_wallet_transactions', ['payos_order_code'], unique=True)

    op.create_table(
        'chat_messages',
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('center_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('scope', sa.Enum('direct', 'class', name='chat_scopes'), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=True),
        sa.Column('recipient_id', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_pinned', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['center_id'], ['centers.center_id']),
        sa.ForeignKeyConstraint(['sender_id'], ['users.user_id']),
        sa.ForeignKeyConstraint(['class_id'], ['classrooms.class_id']),
        sa.ForeignKeyConstraint(['recipient_id'], ['users.user_id']),
        sa.PrimaryKeyConstraint('message_id'),
    )
    op.create_index(op.f('ix_chat_messages_message_id'), 'chat_messages', ['message_id'], unique=False)
    op.create_index(op.f('ix_chat_messages_center_id'), 'chat_messages', ['center_id'], unique=False)
    op.create_index(op.f('ix_chat_messages_class_id'), 'chat_messages', ['class_id'], unique=False)
    op.create_index(op.f('ix_chat_messages_created_at'), 'chat_messages', ['created_at'], unique=False)

    op.create_table(
        'exam_progress',
        sa.Column('progress_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('center_id', sa.Integer(), nullable=True),
        sa.Column('exam_id', sa.Integer(), nullable=True),
        sa.Column('skill', sa.String(length=30), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('questions_done', sa.Integer(), nullable=True),
        sa.Column('total_questions', sa.Integer(), nullable=True),
        sa.Column('last_question', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id']),
        sa.ForeignKeyConstraint(['center_id'], ['centers.center_id']),
        sa.PrimaryKeyConstraint('progress_id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index(op.f('ix_exam_progress_progress_id'), 'exam_progress', ['progress_id'], unique=False)
    op.create_index(op.f('ix_exam_progress_user_id'), 'exam_progress', ['user_id'], unique=True)
    op.create_index(op.f('ix_exam_progress_center_id'), 'exam_progress', ['center_id'], unique=False)


def downgrade() -> None:
    op.drop_table('exam_progress')
    op.drop_table('chat_messages')
    op.drop_table('center_wallet_transactions')
    op.drop_table('class_members')
    op.drop_table('center_memberships')
    op.drop_table('classrooms')
    op.drop_table('centers')
    op.execute(
        "ALTER TABLE users MODIFY COLUMN role "
        "ENUM('admin','student','customer')"
    )
