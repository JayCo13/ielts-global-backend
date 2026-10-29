-- Wipe ALL VIP data before launch (site has no real users yet):
-- every VIP package, subscription and payment transaction; VIP flags reset.
-- Run: python3 apply_sql_file.py sql/delete_all_vip_2026_10_29.sql
USE ielts_practice_db;

SELECT (SELECT COUNT(*) FROM vip_packages) AS packages, (SELECT COUNT(*) FROM vip_subscriptions) AS subscriptions, (SELECT COUNT(*) FROM package_transactions) AS transactions;

-- center_wallet_transactions.package_id is a nullable FK to vip_packages
UPDATE center_wallet_transactions SET package_id = NULL WHERE package_id IS NOT NULL;
-- affiliate commissions credited from these transactions
DELETE FROM affiliate_wallet_tx WHERE type = 'commission' AND source_transaction_id IS NOT NULL;

DELETE FROM package_transactions;
DELETE FROM vip_subscriptions;
DELETE FROM vip_packages;
UPDATE users SET is_vip = 0, vip_expiry = NULL WHERE is_vip = 1 OR vip_expiry IS NOT NULL;

SELECT (SELECT COUNT(*) FROM vip_packages) AS packages, (SELECT COUNT(*) FROM vip_subscriptions) AS subscriptions, (SELECT COUNT(*) FROM package_transactions) AS transactions, (SELECT COUNT(*) FROM users WHERE is_vip = 1) AS vip_users;
