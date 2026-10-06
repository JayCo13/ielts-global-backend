"""affiliate referral program

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-07-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users: referral columns
    op.add_column('users', sa.Column('referral_code', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('referred_by', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('affiliate_balance', sa.BigInteger(), nullable=False, server_default='0'))
    op.create_index(op.f('ix_users_referral_code'), 'users', ['referral_code'], unique=True)
    op.create_foreign_key('fk_users_referred_by', 'users', 'users', ['referred_by'], ['user_id'])

    # affiliate wallet ledger
    op.create_table(
        'affiliate_wallet_tx',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.Enum('commission', 'withdraw', 'withdraw_refund', name='affiliate_tx_types'), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('balance_after', sa.BigInteger(), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('source_user_id', sa.Integer(), nullable=True),
        sa.Column('source_transaction_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id']),
        sa.ForeignKeyConstraint(['source_user_id'], ['users.user_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_affiliate_wallet_tx_id'), 'affiliate_wallet_tx', ['id'], unique=False)
    op.create_index(op.f('ix_affiliate_wallet_tx_user_id'), 'affiliate_wallet_tx', ['user_id'], unique=False)
    op.create_index(op.f('ix_affiliate_wallet_tx_source_transaction_id'), 'affiliate_wallet_tx', ['source_transaction_id'], unique=True)

    # withdrawal requests
    op.create_table(
        'affiliate_withdrawals',
        sa.Column('withdrawal_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.BigInteger(), nullable=False),
        sa.Column('account_holder', sa.String(length=255), nullable=False),
        sa.Column('account_number', sa.String(length=64), nullable=False),
        sa.Column('bank', sa.String(length=255), nullable=False),
        sa.Column('status', sa.Enum('pending', 'paid', 'rejected', name='affiliate_withdrawal_status'), nullable=True),
        sa.Column('admin_note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id']),
        sa.PrimaryKeyConstraint('withdrawal_id'),
    )
    op.create_index(op.f('ix_affiliate_withdrawals_withdrawal_id'), 'affiliate_withdrawals', ['withdrawal_id'], unique=False)
    op.create_index(op.f('ix_affiliate_withdrawals_user_id'), 'affiliate_withdrawals', ['user_id'], unique=False)
    op.create_index(op.f('ix_affiliate_withdrawals_status'), 'affiliate_withdrawals', ['status'], unique=False)


def downgrade() -> None:
    op.drop_table('affiliate_withdrawals')
    op.drop_index(op.f('ix_affiliate_wallet_tx_source_transaction_id'), table_name='affiliate_wallet_tx')
    op.drop_index(op.f('ix_affiliate_wallet_tx_user_id'), table_name='affiliate_wallet_tx')
    op.drop_index(op.f('ix_affiliate_wallet_tx_id'), table_name='affiliate_wallet_tx')
    op.drop_table('affiliate_wallet_tx')
    op.drop_constraint('fk_users_referred_by', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_referral_code'), table_name='users')
    op.drop_column('users', 'affiliate_balance')
    op.drop_column('users', 'referred_by')
    op.drop_column('users', 'referral_code')
