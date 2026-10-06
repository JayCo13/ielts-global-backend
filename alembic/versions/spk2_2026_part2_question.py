"""Speaking: add the Part 2 long turn as its own question row.

The cue card used to live only on the topic, which left the long turn with no
question_id — and generated content (docs/speaking-spec.md §2.3), as well as the
student's recording later, both hang off one. Adding 'part2' to the enum lets the long
turn be stored like every other question instead of needing its own special case.

Existing rows are untouched: widening a MySQL ENUM keeps stored values valid.

Revision ID: spk2_2026
Revises: spk1_2026
"""
from alembic import op
import sqlalchemy as sa

revision = 'spk2_2026'
down_revision = 'spk1_2026'
branch_labels = None
depends_on = None

_OLD = sa.Enum('part1', 'part2_followup', 'part3', name='speaking_question_parts')
_NEW = sa.Enum('part1', 'part2', 'part2_followup', 'part3', name='speaking_question_parts')


def upgrade():
    op.alter_column('speaking_questions', 'part', existing_type=_OLD, type_=_NEW,
                    existing_nullable=False)


def downgrade():
    # Only reversible once no long-turn rows are left, so drop them first.
    op.execute("DELETE FROM speaking_questions WHERE part = 'part2'")
    op.alter_column('speaking_questions', 'part', existing_type=_NEW, type_=_OLD,
                    existing_nullable=False)
