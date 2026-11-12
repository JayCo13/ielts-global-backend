-- Creator Hub feedback (2026-11-11): channel Initial Views + "Others" platform name.
-- Same as alembic revision creator_chan_2026 (revises creator_proof_2026).
-- Run on prod TiDB BEFORE deploying the backend:
--   python3 apply_sql_file.py sql/creator_channels_2026_11b.sql
-- Re-runnable (IF NOT EXISTS).
USE ielts_practice_db;

ALTER TABLE creator_channels ADD COLUMN IF NOT EXISTS platform_name VARCHAR(100) NULL;

ALTER TABLE creator_channels ADD COLUMN IF NOT EXISTS initial_views BIGINT NOT NULL DEFAULT 0;

-- Mark the alembic head so a later `alembic upgrade` doesn't try to re-add the columns.
UPDATE alembic_version SET version_num = 'creator_chan_2026' WHERE version_num = 'creator_proof_2026';
