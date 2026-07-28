"""add payos columns to package_transactions

Adds payos_order_code (BigInteger, unique, indexed) and payos_checkout_url (Text)
to support the PayOS payment gateway. Additive only — no existing column is altered.

Revision ID: e5a1c7d2f9b4
Revises: c8e4f1d9a7b3
Create Date: 2026-07-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5a1c7d2f9b4'
down_revision: Union[str, None] = 'c8e4f1d9a7b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'package_transactions',
        sa.Column('payos_order_code', sa.BigInteger(), nullable=True)
    )
    op.add_column(
        'package_transactions',
        sa.Column('payos_checkout_url', sa.Text(), nullable=True)
    )
    op.create_index(
        'ix_package_transactions_payos_order_code',
        'package_transactions',
        ['payos_order_code'],
        unique=True
    )


def downgrade() -> None:
    op.drop_index(
        'ix_package_transactions_payos_order_code',
        table_name='package_transactions'
    )
    op.drop_column('package_transactions', 'payos_checkout_url')
    op.drop_column('package_transactions', 'payos_order_code')
