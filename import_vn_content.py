"""Replace the global DB's test content with the VN content import file.

Run by a human, never from an automated session:

    python import_vn_content.py backup                      # 1) dump the tables that will be replaced
    python import_vn_content.py apply  <vn_content_import.sql>   # 2) run the import
    python import_vn_content.py apply  <file> --start N     #    resume after a failure
    python import_vn_content.py verify <file>.counts.json   # 3) compare row counts

Target is the production TiDB (`TiDB` in .env) unless IMPORT_DB_URL is set (local testing).

Unlike apply_sql_file.py this sends each statement to the driver untouched: the
content is full of ':' and '%' that SQLAlchemy's text() would read as bind
parameters. FOREIGN_KEY_CHECKS=0 is re-issued on every run so a resumed import
behaves like the original one.
"""
import gzip
import json
import os
import sys
import time

import pymysql
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv()

# Tables the import truncates: the content itself, and the per-user history that
# points at it. Accounts, subscriptions and payments are never touched.
CONTENT_TABLES = [
    "exams", "exam_access_types", "exam_sections", "question_groups", "questions",
    "question_options", "reading_passages", "listening_media", "writing_tasks",
    "listening_alignments", "listening_cue_overrides", "speaking_topics",
    "speaking_questions", "speaking_suggestions", "speaking_vocabularies",
    "speaking_materials", "speaking_material_access_types", "speaking_pron_units",
    "speaking_pron_items", "dictation_units", "dictation_words",
]
HISTORY_TABLES = [
    "student_answers", "listening_answers", "result_answer_snapshots", "error_reports",
    "exam_progress", "homeworks", "exam_results", "writing_answers", "writing_attempts",
    "speaking_question_progress", "speaking_attempt_answers", "speaking_attempts",
    "student_important_words", "student_history_archives",
]


def connect(streaming=False):
    override = os.getenv("IMPORT_DB_URL")
    raw = override or os.getenv("TiDB")
    if not raw:
        print("ERROR: TiDB connection string not found in .env")
        sys.exit(1)
    url = make_url(raw)
    label = "LOCAL TEST database" if override else "PRODUCTION TiDB"
    print(f"Target: {label} '{url.database}' on {url.host}")
    if not override and input(f"Type the database name ({url.database}) to continue: ").strip() != url.database:
        print("Aborted.")
        sys.exit(1)
    kwargs = dict(
        host=url.host, port=url.port or 4000, user=url.username, password=url.password or "",
        database=url.database, charset="utf8mb4", autocommit=True,
        read_timeout=600, write_timeout=600,
    )
    if not override:
        kwargs["ssl"] = {"ssl_disabled": False}   # same as apply_sql_file.py: force TLS
    if streaming:
        kwargs["cursorclass"] = pymysql.cursors.SSCursor
    return pymysql.connect(**kwargs)


def backup():
    conn = connect(streaming=True)
    out = os.path.abspath(f"global_content_backup_{time.strftime('%Y%m%d_%H%M%S')}.sql.gz")
    total = 0
    with gzip.open(out, "wb", compresslevel=3) as f:
        f.write(b"SET FOREIGN_KEY_CHECKS=0;\n")
        for table in HISTORY_TABLES + CONTENT_TABLES:
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM `{table}`")
            cols = ",".join(f"`{d[0]}`" for d in cur.description)
            f.write(f"TRUNCATE TABLE `{table}`;\n".encode())
            n = 0
            for row in cur:
                values = ",".join(conn.escape(v) if not isinstance(v, (bytes, bytearray))
                                  else "0x" + bytes(v).hex() if v else "''" for v in row)
                f.write(f"INSERT INTO `{table}` ({cols}) VALUES ({values});\n".encode("utf-8"))
                n += 1
            cur.close()
            total += n
            print(f"  {table}: {n} rows")
        f.write(b"SET FOREIGN_KEY_CHECKS=1;\n")
    conn.close()
    print(f"\nBackup written: {out} ({total} rows, {os.path.getsize(out) / 1e6:.1f} MB)")
    print("To roll back later: python import_vn_content.py apply <that file>")


def apply(path, start):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as f:
        statements = [l.strip().rstrip(b";") for l in f if l.strip() and not l.startswith(b"--")]
    print(f"{path}: {len(statements)} statements" + (f", starting at {start}" if start > 1 else ""))
    conn = connect()
    cur = conn.cursor()
    cur.execute("SET FOREIGN_KEY_CHECKS=0")
    t0 = time.time()
    for i, stmt in enumerate(statements, 1):
        if i < start:
            continue
        head = stmt[:70].decode("utf-8", "replace")
        try:
            cur.execute(stmt)
        except Exception as e:  # stop at the first failure; nothing later has run
            print(f"\n[{i}/{len(statements)}] FAILED {head}\n{type(e).__name__}: {str(e)[:600]}")
            print(f"\nStopped. Statements 1..{i - 1} are applied. After fixing, resume with: --start {i}")
            sys.exit(2)
        if i % 25 == 0 or i == len(statements) or len(stmt) < 200:
            print(f"[{i}/{len(statements)}] OK   {head}  ({time.time() - t0:.0f}s)")
    cur.execute("SET FOREIGN_KEY_CHECKS=1")
    conn.close()
    print("\nDone. All statements applied.")


def verify(counts_path):
    with open(counts_path, encoding="utf-8") as f:
        expected = json.load(f)
    conn = connect()
    cur = conn.cursor()
    bad = 0
    for table, want in expected.items():
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        got = cur.fetchone()[0]
        flag = "OK  " if got == want else "DIFF"
        bad += got != want
        print(f"  {flag} {table}: {got} (expected {want})")
    cur.execute("SELECT COUNT(*) FROM `listening_media` WHERE `audio_url` IS NULL")
    print(f"  listening parts without an audio URL: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM `users`")
    print(f"  users (untouched): {cur.fetchone()[0]}")
    conn.close()
    print("\nAll counts match." if not bad else f"\n{bad} table(s) differ.")
    sys.exit(1 if bad else 0)


def main():
    argv = sys.argv[1:]
    start = 1
    if "--start" in argv:
        idx = argv.index("--start")
        start = int(argv[idx + 1])
        del argv[idx:idx + 2]
    if argv[:1] == ["backup"] and len(argv) == 1:
        backup()
    elif argv[:1] == ["apply"] and len(argv) == 2:
        apply(argv[1], start)
    elif argv[:1] == ["verify"] and len(argv) == 2:
        verify(argv[1])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
