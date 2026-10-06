"""Clear listening part_title rows that were over-populated by an earlier
fix attempt.

Revision ID: b1f4c2d9a3e7
Revises: fdd7f49aface
Create Date: 2026-05-14 00:00:00.000000

An earlier iteration of this migration copied `exam_sections.description`
into `exam_sections.part_title` for listening rows whose `part_title` was
empty. That turned out to be wrong: the two columns hold *different*
things — `description` is the long per-part description (edited via the
EditListeningTest popup), and `part_title` is the short label shown on
/manage_part_titles and on the student listening card.

To undo only the rows the original backfill touched (without disturbing
rows whose `part_title` is a legitimate short label), clear `part_title`
on rows where its value is identical to `description`. The two columns
are LONGTEXT, so equality is safe; legitimate titles are short and would
virtually never equal the long description payload exactly.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b1f4c2d9a3e7'
down_revision: Union[str, None] = 'fdd7f49aface'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE exam_sections
        SET part_title = NULL
        WHERE section_type = 'listening'
          AND part_title IS NOT NULL
          AND description IS NOT NULL
          AND part_title = description
        """
    )


def downgrade() -> None:
    # Non-destructive correction; nothing meaningful to undo.
    pass
