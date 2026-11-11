-- Creator Hub feedback (2026-11-10): proof of views on each views-submission line.
-- Same as alembic revision creator_proof_2026 (revises creator_hub_2026).
-- Run on prod TiDB BEFORE deploying the backend:
--   python apply_sql_file.py sql/creator_proofs_2026_11.sql
-- Re-runnable (IF NOT EXISTS).
USE ielts_practice_db;

ALTER TABLE creator_reconciliation_items ADD COLUMN IF NOT EXISTS proof_urls TEXT NULL;

-- Mark the alembic head so a later `alembic upgrade` doesn't try to re-add the column.
UPDATE alembic_version SET version_num = 'creator_proof_2026' WHERE version_num = 'creator_hub_2026';
