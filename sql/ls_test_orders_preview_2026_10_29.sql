-- PREVIEW ONLY (no changes). Lemon Squeezy one-time test orders made while the
-- store was in Test mode, plus anything that depends on them.
-- Run: python3 apply_sql_file.py sql/ls_test_orders_preview_2026_10_29.sql
USE ielts_practice_db;

-- 1. The test transactions (one-time LS orders, incl. refunded ones)
SELECT t.transaction_id, t.subscription_id, t.user_id, u.email, t.package_id, t.amount, t.ls_order_id, t.status, t.created_at
FROM package_transactions t JOIN users u ON u.user_id = t.user_id
WHERE t.payment_method = 'lemonsqueezy'
  AND (t.admin_note LIKE 'Lemon Squeezy one-time order%' OR t.admin_note LIKE 'Refunded on Lemon Squeezy%')
ORDER BY t.created_at;

-- 2. Their VIP subscriptions
SELECT s.subscription_id, s.user_id, s.package_id, s.start_date, s.end_date, s.payment_status
FROM vip_subscriptions s
WHERE s.subscription_id IN (
  SELECT t.subscription_id FROM package_transactions t
  WHERE t.payment_method = 'lemonsqueezy'
    AND (t.admin_note LIKE 'Lemon Squeezy one-time order%' OR t.admin_note LIKE 'Refunded on Lemon Squeezy%'));

-- 3. Affiliate commissions credited from these orders (should be empty)
SELECT w.id, w.user_id, w.amount, w.source_transaction_id, w.created_at
FROM affiliate_wallet_tx w
WHERE w.source_transaction_id IN (
  SELECT t.transaction_id FROM package_transactions t
  WHERE t.payment_method = 'lemonsqueezy'
    AND (t.admin_note LIKE 'Lemon Squeezy one-time order%' OR t.admin_note LIKE 'Refunded on Lemon Squeezy%'));
