"""
Migration: PayPal → Lemon Squeezy
Add new columns for Lemon Squeezy integration to TiDB.

Changes:
1. vip_packages: Add ls_variant_id
2. vip_subscriptions: Add ls_subscription_id, ls_customer_id, is_auto_renew, cancelled_at
3. vip_subscriptions: Add 'expired' to payment_status enum
4. package_transactions: Rename paypal_order_id → ls_order_id
"""
import os
import sys
from dotenv import load_dotenv

# Load env from the backend directory
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from sqlalchemy import create_engine, text

# Get TiDB connection string
db_url = os.getenv("TiDB")
if not db_url:
    print("ERROR: TiDB connection string not found in .env")
    sys.exit(1)

# For local dev: relax SSL verification (remove strict cert checks)
# The server still uses TLS encryption, we just skip CA verification
import re
db_url = re.sub(r'ssl_ca=[^&]*&?', '', db_url)
db_url = re.sub(r'ssl_verify_cert=[^&]*&?', '', db_url)
db_url = re.sub(r'ssl_verify_identity=[^&]*&?', '', db_url)
# Clean up trailing ? or &
db_url = db_url.rstrip('?').rstrip('&')

print(f"Connecting to TiDB...")
engine = create_engine(db_url, connect_args={"ssl": {"ssl_disabled": False}})

migrations = [
    # 1. Add ls_variant_id to vip_packages
    {
        "name": "Add ls_variant_id to vip_packages",
        "check": "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vip_packages' AND COLUMN_NAME = 'ls_variant_id'",
        "sql": "ALTER TABLE vip_packages ADD COLUMN ls_variant_id VARCHAR(50) NULL"
    },
    # 2. Add ls_subscription_id to vip_subscriptions
    {
        "name": "Add ls_subscription_id to vip_subscriptions",
        "check": "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vip_subscriptions' AND COLUMN_NAME = 'ls_subscription_id'",
        "sql": "ALTER TABLE vip_subscriptions ADD COLUMN ls_subscription_id VARCHAR(100) NULL"
    },
    # 3. Add index on ls_subscription_id
    {
        "name": "Add index on ls_subscription_id",
        "check": "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vip_subscriptions' AND COLUMN_NAME = 'ls_subscription_id'",
        "sql": "ALTER TABLE vip_subscriptions ADD INDEX idx_ls_subscription_id (ls_subscription_id)"
    },
    # 4. Add ls_customer_id to vip_subscriptions
    {
        "name": "Add ls_customer_id to vip_subscriptions",
        "check": "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vip_subscriptions' AND COLUMN_NAME = 'ls_customer_id'",
        "sql": "ALTER TABLE vip_subscriptions ADD COLUMN ls_customer_id VARCHAR(100) NULL"
    },
    # 5. Add is_auto_renew to vip_subscriptions
    {
        "name": "Add is_auto_renew to vip_subscriptions",
        "check": "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vip_subscriptions' AND COLUMN_NAME = 'is_auto_renew'",
        "sql": "ALTER TABLE vip_subscriptions ADD COLUMN is_auto_renew TINYINT(1) DEFAULT 1"
    },
    # 6. Add cancelled_at to vip_subscriptions
    {
        "name": "Add cancelled_at to vip_subscriptions",
        "check": "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vip_subscriptions' AND COLUMN_NAME = 'cancelled_at'",
        "sql": "ALTER TABLE vip_subscriptions ADD COLUMN cancelled_at DATETIME NULL"
    },
    # 7. Rename paypal_order_id → ls_order_id on package_transactions
    {
        "name": "Rename paypal_order_id to ls_order_id",
        "check": "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'package_transactions' AND COLUMN_NAME = 'ls_order_id'",
        "sql": "ALTER TABLE package_transactions CHANGE COLUMN paypal_order_id ls_order_id VARCHAR(100) NULL"
    },
    # 8. Update payment_status enum to include 'expired' 
    # TiDB/MySQL: modify the column to add the new enum value
    {
        "name": "Add 'expired' to payment_status enum",
        "check": "SELECT COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'vip_subscriptions' AND COLUMN_NAME = 'payment_status' AND COLUMN_TYPE LIKE '%expired%'",
        "sql": "ALTER TABLE vip_subscriptions MODIFY COLUMN payment_status ENUM('pending', 'completed', 'reject', 'expired') NULL"
    },
]

print("=" * 60)
print("Running Lemon Squeezy migration on TiDB")
print("=" * 60)

with engine.connect() as conn:
    for m in migrations:
        # Check if already applied
        result = conn.execute(text(m["check"])).fetchone()
        if result:
            print(f"  ✓ SKIP: {m['name']} (already exists)")
            continue
        
        try:
            conn.execute(text(m["sql"]))
            conn.commit()
            print(f"  ✅ OK: {m['name']}")
        except Exception as e:
            print(f"  ❌ FAIL: {m['name']} — {e}")

print("=" * 60)
print("Migration complete!")
print("=" * 60)
