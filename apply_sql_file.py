"""
Run a .sql file against the PRODUCTION TiDB database, statement by statement.

Reads the `TiDB` DSN from .env (same one migrate_lemonsqueezy.py uses), so no
mysql client, host or password has to be typed. Stops at the first failing
statement and reports its number, so a partial run can be resumed with --start.

    python3 apply_sql_file.py sql/vn_port_schema_2026_10_ielts_practice_db.sql
    python3 apply_sql_file.py <file.sql> --start 120     # resume from statement 120
    python3 apply_sql_file.py <file.sql> --dry-run       # only list statements

This is run by a human on purpose (see CLAUDE.md: no automated prod migrations).
"""
import os
import re
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

from sqlalchemy import create_engine, text  # noqa: E402


def split_statements(sql: str):
    """Split on ';' at end of line; drop comment-only chunks."""
    out = []
    for chunk in re.split(r';[ \t]*\n', sql + '\n'):
        lines = [l for l in chunk.split('\n') if l.strip() and not l.strip().startswith('--')]
        stmt = '\n'.join(lines).strip().rstrip(';').strip()
        if stmt:
            out.append(stmt)
    return out


def main():
    argv = sys.argv[1:]
    dry_run = '--dry-run' in argv
    start = 1
    if '--start' in argv:
        # consume the flag and its value so the number isn't mistaken for a file
        idx = argv.index('--start')
        start = int(argv[idx + 1])
        del argv[idx:idx + 2]
    args = [a for a in argv if not a.startswith('--')]
    if len(args) != 1:
        print(__doc__)
        sys.exit(1)
    path = args[0]

    with open(path, encoding='utf-8') as f:
        statements = split_statements(f.read())
    print(f"{path}: {len(statements)} statements")

    if dry_run:
        for i, s in enumerate(statements, 1):
            print(f"[{i}] {s.splitlines()[0][:110]}")
        return

    db_url = os.getenv("TiDB")
    if not db_url:
        print("ERROR: TiDB connection string not found in .env")
        sys.exit(1)
    # Same handling as migrate_lemonsqueezy.py: drop file-based SSL params, force TLS.
    db_url = re.sub(r'ssl_ca=[^&]*&?', '', db_url)
    db_url = re.sub(r'ssl_verify_cert=[^&]*&?', '', db_url)
    db_url = re.sub(r'ssl_verify_identity=[^&]*&?', '', db_url)
    db_url = db_url.rstrip('?&')
    db_name = db_url.rsplit('/', 1)[-1].split('?')[0]

    print(f"Target: PRODUCTION TiDB, database '{db_name}'" + (f", starting at statement {start}" if start > 1 else ""))
    if input(f"Type the database name ({db_name}) to continue: ").strip() != db_name:
        print("Aborted.")
        sys.exit(1)

    engine = create_engine(db_url, connect_args={"ssl": {"ssl_disabled": False}})
    with engine.connect() as conn:
        for i, stmt in enumerate(statements, 1):
            if i < start:
                continue
            head = stmt.splitlines()[0][:100]
            try:
                result = conn.execute(text(stmt))
                conn.commit()
                if result.returns_rows:
                    rows = result.fetchall()
                    print(f"[{i}/{len(statements)}] OK   {head}\n        -> {rows if rows else 'empty set'}")
                else:
                    print(f"[{i}/{len(statements)}] OK   {head}")
            except Exception as e:  # stop at the first failure; nothing later has run
                print(f"\n[{i}/{len(statements)}] FAILED {head}\n{type(e).__name__}: {str(e)[:600]}")
                print(f"\nStopped. Statements 1..{i - 1} are applied. After fixing, resume with: --start {i}")
                sys.exit(2)
    print("\nDone. All statements applied.")


if __name__ == "__main__":
    main()
