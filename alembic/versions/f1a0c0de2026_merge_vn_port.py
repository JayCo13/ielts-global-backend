"""Merge the Vietnam-tree schema (ported from ielts-main-nov) into the global head.

Revision ID: f1a0c0de2026
Revises: e5a1c7d2f9b4, sstaskspk_2026
Create Date: 2026-10-06 00:00:00.000000

The VN revisions branch from the shared `accf7c73e8f7`; the global PayOS chain
(`b7f3a2c8d9e1` → `c8e4f1d9a7b3` → `e5a1c7d2f9b4`) branches from the same point.
They touch disjoint tables/columns, so this is a plain merge with no operations.
"""
from typing import Sequence, Union


revision: str = 'f1a0c0de2026'
down_revision: Union[str, Sequence[str], None] = ('e5a1c7d2f9b4', 'sstaskspk_2026')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
