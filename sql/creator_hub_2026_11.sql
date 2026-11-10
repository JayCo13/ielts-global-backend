-- Creator Hub: access control, channels, views reconciliation, shared wallet.
-- Same as alembic revision creator_hub_2026 (revises creator_prog_2026).
-- Run on prod TiDB AFTER sql/creator_program_2026_11.sql and BEFORE deploying the backend:
--   python apply_sql_file.py sql/creator_hub_2026_11.sql
-- Re-runnable (IF NOT EXISTS / NOT EXISTS guards). Run from the file — don't paste the ENUM line.
USE ielts_practice_db;

ALTER TABLE creator_applications ADD COLUMN IF NOT EXISTS reject_reason TEXT NULL;
ALTER TABLE creator_applications ADD COLUMN IF NOT EXISTS reviewed_at DATETIME NULL;
ALTER TABLE creator_applications ADD COLUMN IF NOT EXISTS reviewed_by INT NULL;
ALTER TABLE creator_applications ADD COLUMN IF NOT EXISTS source VARCHAR(20) NULL;
ALTER TABLE creator_applications ADD COLUMN IF NOT EXISTS country_other VARCHAR(100) NULL;

-- Shared wallet: add the 'creator_reward' transaction type (existing values unchanged).
ALTER TABLE affiliate_wallet_tx MODIFY COLUMN type ENUM('commission','withdraw','withdraw_refund','creator_reward') NOT NULL;

CREATE TABLE IF NOT EXISTS creator_profiles (
  user_id INT NOT NULL PRIMARY KEY,
  hub_access TINYINT(1) NOT NULL DEFAULT 0,
  access_source VARCHAR(20) NULL,
  full_name VARCHAR(120) NULL,
  phone VARCHAR(40) NULL,
  email VARCHAR(255) NULL,
  country_code VARCHAR(8) NULL,
  enabled_at DATETIME NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL,
  KEY ix_creator_profiles_hub_access (hub_access),
  CONSTRAINT fk_creator_profiles_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS creator_access_logs (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  action VARCHAR(20) NOT NULL,
  source VARCHAR(20) NULL,
  note TEXT NULL,
  actor_id INT NULL,
  created_at DATETIME NULL,
  KEY ix_creator_access_logs_user_id (user_id),
  CONSTRAINT fk_creator_access_logs_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS creator_channels (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  platform VARCHAR(20) NOT NULL,
  channel_name VARCHAR(255) NOT NULL,
  channel_url VARCHAR(500) NOT NULL,
  url_key VARCHAR(500) NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  status_reason TEXT NULL,
  is_primary TINYINT(1) NOT NULL DEFAULT 0,
  approved_total_views BIGINT NOT NULL DEFAULT 0,
  last_submitted_total BIGINT NULL,
  last_submitted_at DATETIME NULL,
  reviewed_at DATETIME NULL,
  reviewed_by INT NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL,
  KEY ix_creator_channels_user_id (user_id),
  KEY ix_creator_channels_status (status),
  KEY ix_creator_channels_url_key (url_key(191)),
  CONSTRAINT fk_creator_channels_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS creator_channel_logs (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  channel_id INT NOT NULL,
  from_status VARCHAR(20) NULL,
  to_status VARCHAR(20) NOT NULL,
  reason TEXT NULL,
  actor_id INT NULL,
  created_at DATETIME NULL,
  KEY ix_creator_channel_logs_channel_id (channel_id),
  CONSTRAINT fk_creator_channel_logs_channel FOREIGN KEY (channel_id) REFERENCES creator_channels (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS creator_reconciliations (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'submitted',
  estimated_usd FLOAT NOT NULL DEFAULT 0,
  approved_units BIGINT NULL,
  reject_reason TEXT NULL,
  wallet_tx_id INT NULL,
  submitted_at DATETIME NULL,
  reviewed_at DATETIME NULL,
  reviewed_by INT NULL,
  KEY ix_creator_reconciliations_user_id (user_id),
  KEY ix_creator_reconciliations_status (status),
  KEY ix_creator_reconciliations_submitted_at (submitted_at),
  CONSTRAINT fk_creator_reconciliations_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS creator_reconciliation_items (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  reconciliation_id INT NOT NULL,
  channel_id INT NOT NULL,
  channel_type VARCHAR(10) NOT NULL,
  previous_total BIGINT NOT NULL DEFAULT 0,
  submitted_total BIGINT NOT NULL DEFAULT 0,
  period_views BIGINT NOT NULL DEFAULT 0,
  views_date DATE NULL,
  rate_per_k FLOAT NOT NULL DEFAULT 0,
  estimated_usd FLOAT NOT NULL DEFAULT 0,
  KEY ix_creator_reconciliation_items_reconciliation_id (reconciliation_id),
  KEY ix_creator_reconciliation_items_channel_id (channel_id),
  CONSTRAINT fk_creator_recon_items_recon FOREIGN KEY (reconciliation_id) REFERENCES creator_reconciliations (id) ON DELETE CASCADE,
  CONSTRAINT fk_creator_recon_items_channel FOREIGN KEY (channel_id) REFERENCES creator_channels (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

UPDATE alembic_version SET version_num = 'creator_hub_2026' WHERE version_num = 'creator_prog_2026';
SELECT version_num FROM alembic_version;
