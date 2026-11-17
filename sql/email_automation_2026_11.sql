-- Lifecycle emails (2026-11-14, "chính sách email mới"): welcome email + VIP checkout reminder.
-- Same as alembic revision email_auto_2026 (revises creator_chan_2026).
-- Run on prod TiDB BEFORE deploying the backend:
--   python3 apply_sql_file.py sql/email_automation_2026_11.sql
-- Re-runnable (IF NOT EXISTS).
USE ielts_practice_db;

CREATE TABLE IF NOT EXISTS checkout_intents (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  package_id INT NULL,
  created_at DATETIME NULL,
  KEY ix_checkout_intents_user_id (user_id),
  KEY ix_checkout_intents_created_at (created_at),
  CONSTRAINT fk_checkout_intents_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS marketing_email_logs (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  kind VARCHAR(40) NOT NULL,
  ref VARCHAR(64) NOT NULL DEFAULT '',
  email VARCHAR(255) NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'sending',
  created_at DATETIME NULL,
  UNIQUE KEY uq_marketing_email_user_kind_ref (user_id, kind, ref),
  KEY ix_marketing_email_logs_user_id (user_id),
  KEY ix_marketing_email_logs_created_at (created_at),
  CONSTRAINT fk_marketing_email_logs_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

-- Mark the alembic head so a later `alembic upgrade` doesn't try to re-create the tables.
UPDATE alembic_version SET version_num = 'email_auto_2026' WHERE version_num = 'creator_chan_2026';
