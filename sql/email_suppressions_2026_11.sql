-- Email suppression list (marketing unsubscribes, hard bounces, spam complaints).
-- Same as alembic revision email_supp_2026 (revises f2b1e0a7c3d9).
-- Run on prod TiDB FIRST, before sql/creator_program_2026_11.sql:
--   python apply_sql_file.py sql/email_suppressions_2026_11.sql
-- Re-runnable (IF NOT EXISTS guard).
USE ielts_practice_db;

CREATE TABLE IF NOT EXISTS email_suppressions (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  reason VARCHAR(20) NOT NULL,
  created_at DATETIME NULL,
  UNIQUE KEY ix_email_suppressions_email (email)
) DEFAULT CHARSET=utf8mb4;

UPDATE alembic_version SET version_num = 'email_supp_2026' WHERE version_num = 'f2b1e0a7c3d9';
SELECT version_num FROM alembic_version;
