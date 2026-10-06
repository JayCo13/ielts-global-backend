"""Speaking: storage for the pre-generated examiner voice.

docs/speaking-spec.md decision #3. One row per spoken line per voice. `cache_key` is a
flat string ("question:12", "script:opening", "topic-intro:4") so a new kind of clip
needs no schema change, and the unique index on (cache_key, voice) is what makes the
generation job safe to re-run.

Revision ID: spk3_2026
Revises: spk2_2026
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB

revision = 'spk3_2026'
down_revision = 'spk2_2026'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'speaking_tts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cache_key', sa.String(length=64), nullable=False),
        sa.Column('voice', sa.String(length=32), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('audio', LONGBLOB(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cache_key', 'voice', name='uq_speaking_tts_key_voice'),
    )
    op.create_index('ix_speaking_tts_cache_key', 'speaking_tts', ['cache_key'])
    op.create_index('ix_speaking_tts_id', 'speaking_tts', ['id'])


def downgrade():
    op.drop_index('ix_speaking_tts_id', table_name='speaking_tts')
    op.drop_index('ix_speaking_tts_cache_key', table_name='speaking_tts')
    op.drop_table('speaking_tts')
