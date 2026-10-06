"""affiliate payout method (QR + bank)

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-07-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('payout_qr_url', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('payout_bank', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('payout_account_number', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('payout_account_holder', sa.String(length=255), nullable=True))
    op.add_column('affiliate_withdrawals', sa.Column('qr_url', sa.String(length=255), nullable=True))
    # account fields were required before; the payout method now covers QR-only.
    op.alter_column('affiliate_withdrawals', 'account_holder', existing_type=sa.String(length=255), nullable=True)
    op.alter_column('affiliate_withdrawals', 'account_number', existing_type=sa.String(length=64), nullable=True)
    op.alter_column('affiliate_withdrawals', 'bank', existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    op.alter_column('affiliate_withdrawals', 'bank', existing_type=sa.String(length=255), nullable=False)
    op.alter_column('affiliate_withdrawals', 'account_number', existing_type=sa.String(length=64), nullable=False)
    op.alter_column('affiliate_withdrawals', 'account_holder', existing_type=sa.String(length=255), nullable=False)
    op.drop_column('affiliate_withdrawals', 'qr_url')
    op.drop_column('users', 'payout_account_holder')
    op.drop_column('users', 'payout_account_number')
    op.drop_column('users', 'payout_bank')
    op.drop_column('users', 'payout_qr_url')
