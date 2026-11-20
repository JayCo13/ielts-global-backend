-- Creator bonus policy (2026-11-19): per-channel monthly tier bonus, video links, monthly cap.
-- Same as alembic revision creator_bonus_2026 (revises aff_disc_2026).
-- Run on prod TiDB BEFORE deploying the backend, AFTER sql/affiliate_discount_2026_11.sql:
--   python3 apply_sql_file.py sql/creator_bonus_2026_11.sql
-- Re-runnable (IF NOT EXISTS).
USE ielts_practice_db;

ALTER TABLE creator_reconciliations ADD COLUMN IF NOT EXISTS views_reward_raw_usd DOUBLE NULL;
ALTER TABLE creator_reconciliations ADD COLUMN IF NOT EXISTS views_reward_usd DOUBLE NULL;
ALTER TABLE creator_reconciliations ADD COLUMN IF NOT EXISTS tier_bonus_usd DOUBLE NULL;
ALTER TABLE creator_reconciliations ADD COLUMN IF NOT EXISTS video_reward_usd DOUBLE NULL;
ALTER TABLE creator_reconciliation_items ADD COLUMN IF NOT EXISTS tier_views BIGINT NULL;
ALTER TABLE creator_reconciliation_items ADD COLUMN IF NOT EXISTS tier_bonus_usd DOUBLE NULL;

CREATE TABLE IF NOT EXISTS creator_video_links (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  reconciliation_id INT NOT NULL,
  url VARCHAR(500) NOT NULL,
  url_key VARCHAR(500) NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  reward_usd DOUBLE NOT NULL DEFAULT 0,
  created_at DATETIME NULL,
  reviewed_at DATETIME NULL,
  UNIQUE KEY uq_creator_video_links_url_key (url_key),
  KEY ix_creator_video_links_user_id (user_id),
  KEY ix_creator_video_links_reconciliation_id (reconciliation_id),
  KEY ix_creator_video_links_status (status),
  CONSTRAINT fk_cvl_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE,
  CONSTRAINT fk_cvl_recon FOREIGN KEY (reconciliation_id) REFERENCES creator_reconciliations (id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

UPDATE alembic_version SET version_num = 'creator_bonus_2026' WHERE version_num = 'aff_disc_2026';
