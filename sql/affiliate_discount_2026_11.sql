-- Affiliate discount (2026-11-14): admin "Discount Permission" per creator (default OFF).
-- Same as alembic revision aff_disc_2026 (revises email_auto_2026).
-- Run on prod TiDB BEFORE deploying the backend, AFTER sql/email_automation_2026_11.sql:
--   python3 apply_sql_file.py sql/affiliate_discount_2026_11.sql
-- Re-runnable (IF NOT EXISTS).
USE ielts_practice_db;

ALTER TABLE creator_profiles ADD COLUMN IF NOT EXISTS discount_enabled BOOLEAN NOT NULL DEFAULT 0;

-- Mark the alembic head so a later `alembic upgrade` doesn't try to re-add the column.
UPDATE alembic_version SET version_num = 'aff_disc_2026' WHERE version_num = 'email_auto_2026';
