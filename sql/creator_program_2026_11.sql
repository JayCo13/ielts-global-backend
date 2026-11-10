-- Creator program ("Work With Us") + affiliate per-account commission rates.
-- Same as alembic revision creator_prog_2026 (revises email_supp_2026).
-- Run on prod TiDB BEFORE deploying the backend:  python apply_sql_file.py sql/creator_program_2026_11.sql
USE ielts_practice_db;

CREATE TABLE IF NOT EXISTS affiliate_rate_changes (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  rate_pct FLOAT NULL,
  effective_at DATETIME NOT NULL,
  changed_by INT NULL,
  created_at DATETIME NULL,
  KEY ix_affiliate_rate_changes_user_id (user_id),
  KEY ix_affiliate_rate_changes_effective_at (effective_at),
  CONSTRAINT fk_affiliate_rate_changes_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

-- Default-rate history: orders created before now keep 20%, new orders get 5%.
INSERT INTO affiliate_rate_changes (user_id, rate_pct, effective_at, created_at)
SELECT NULL, 20.0, '2000-01-01 00:00:00', DATE_ADD(UTC_TIMESTAMP(), INTERVAL 7 HOUR)
WHERE NOT EXISTS (SELECT 1 FROM affiliate_rate_changes WHERE user_id IS NULL);
INSERT INTO affiliate_rate_changes (user_id, rate_pct, effective_at, created_at)
SELECT NULL, 5.0, DATE_ADD(UTC_TIMESTAMP(), INTERVAL 7 HOUR), DATE_ADD(UTC_TIMESTAMP(), INTERVAL 7 HOUR)
WHERE NOT EXISTS (SELECT 1 FROM affiliate_rate_changes WHERE user_id IS NULL AND rate_pct = 5.0);

CREATE TABLE IF NOT EXISTS creator_applications (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  full_name VARCHAR(120) NOT NULL,
  country_code VARCHAR(8) NULL,
  languages VARCHAR(255) NULL,
  phone VARCHAR(40) NULL,
  email VARCHAR(255) NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'new',
  admin_note TEXT NULL,
  ip_address VARCHAR(64) NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL,
  KEY ix_creator_applications_user_id (user_id),
  KEY ix_creator_applications_status (status),
  KEY ix_creator_applications_created_at (created_at),
  CONSTRAINT fk_creator_applications_user FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE SET NULL
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS creator_showcase_videos (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  video_url VARCHAR(500) NOT NULL,
  platform VARCHAR(20) NULL,
  thumbnail_url VARCHAR(500) NULL,
  handle VARCHAR(100) NULL,
  likes VARCHAR(20) NULL,
  views VARCHAR(20) NULL,
  earned VARCHAR(20) NULL,
  display_order INT NULL,
  is_active TINYINT(1) NULL,
  created_at DATETIME NULL
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS creator_testimonials (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  avatar_url VARCHAR(500) NULL,
  subtitle VARCHAR(200) NULL,
  earned VARCHAR(20) NULL,
  note VARCHAR(255) NULL,
  profile_url VARCHAR(500) NULL,
  display_order INT NULL,
  is_active TINYINT(1) NULL,
  created_at DATETIME NULL
) DEFAULT CHARSET=utf8mb4;

UPDATE alembic_version SET version_num = 'creator_prog_2026' WHERE version_num = 'email_supp_2026';
SELECT version_num FROM alembic_version;
