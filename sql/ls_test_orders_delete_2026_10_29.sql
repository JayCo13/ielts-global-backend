-- DELETE the Lemon Squeezy test orders listed by ls_test_orders_preview_2026_10_29.sql
-- and recompute VIP status of the affected users. Run the preview first.
-- Run: python3 apply_sql_file.py sql/ls_test_orders_delete_2026_10_29.sql
USE ielts_practice_db;

-- Scratch list of what will be removed (TiDB has no CREATE TABLE ... SELECT)
DROP TABLE IF EXISTS tmp_ls_test_cleanup;
CREATE TABLE tmp_ls_test_cleanup (transaction_id INT PRIMARY KEY, subscription_id INT NULL, user_id INT NULL);
INSERT INTO tmp_ls_test_cleanup (transaction_id, subscription_id, user_id)
SELECT t.transaction_id, t.subscription_id, t.user_id FROM package_transactions t
WHERE t.payment_method = 'lemonsqueezy'
  AND (t.admin_note LIKE 'Lemon Squeezy one-time order%' OR t.admin_note LIKE 'Refunded on Lemon Squeezy%');
SELECT COUNT(*) AS transactions_to_delete, COUNT(DISTINCT subscription_id) AS subscriptions_to_delete, COUNT(DISTINCT user_id) AS users_affected FROM tmp_ls_test_cleanup;

DELETE FROM package_transactions WHERE transaction_id IN (SELECT transaction_id FROM tmp_ls_test_cleanup);
DELETE FROM vip_subscriptions WHERE subscription_id IN (SELECT subscription_id FROM tmp_ls_test_cleanup WHERE subscription_id IS NOT NULL);

-- Recompute VIP from what remains (Vietnam time = UTC+7, matching the app)
UPDATE users u
JOIN (SELECT DISTINCT user_id FROM tmp_ls_test_cleanup WHERE user_id IS NOT NULL) x ON x.user_id = u.user_id
LEFT JOIN (SELECT user_id, MAX(end_date) AS max_end FROM vip_subscriptions WHERE payment_status = 'completed' GROUP BY user_id) m ON m.user_id = u.user_id
SET u.vip_expiry = m.max_end,
    u.is_vip = (m.max_end IS NOT NULL AND m.max_end > DATE_ADD(UTC_TIMESTAMP(), INTERVAL 7 HOUR));

SELECT u.user_id, u.email, u.is_vip, u.vip_expiry FROM users u WHERE u.user_id IN (SELECT user_id FROM tmp_ls_test_cleanup);
DROP TABLE tmp_ls_test_cleanup;
