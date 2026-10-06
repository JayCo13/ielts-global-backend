"""Monthly Cup aggregation + month-end snapshot.

- Monthly Cup = a per-skill ranking aggregated over ONE month's ELIGIBLE full-test
  attempts (reading 50–60 min, listening 20–35 min, writing = AI-graded band).
  Ranked by # tests Top-10 (desc), fewer attempts, higher score, faster time —
  the same ordering as the all-time board, but limited to the month.
- At month end the Top 3 per skill are frozen into `monthly_cup_winners`.
- The new Hall of Fame is aggregated from those frozen rows (see leaderboard_routes).

Global has no cron. Triggers (admin-protected):
    POST /admin/monthly-cup/snapshot            (snapshots the PREVIOUS month; run on day 1)
    POST /admin/monthly-cup/backfill?months=12  (idempotent backfill of past months)
Standalone: python -m app.jobs.monthly_cup [backfill N]
"""
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models.models import (
    ExamResult, ExamSection, WritingTask, WritingAnswer, MonthlyCupWinner,
)
from app.utils.datetime_utils import get_vietnam_time

_BIG = 10 ** 9
TOP_N = 10          # a "cup point" = being in a test's Top 10 that month
TOP3 = 3            # frozen winners per month/skill
MIN_TIME = 50 * 60
MAX_TIME = 60 * 60
LISTEN_MIN_TIME = 20 * 60
LISTEN_MAX_TIME = 35 * 60
SKILLS = ("reading", "listening", "writing", "speaking")


def _month_range(year: int, month: int):
    start = datetime(year, month, 1)
    end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    return start, end


def _round_half(x: float) -> float:
    return round(x * 2) / 2.0


def _exam_skill_map(db: Session) -> dict:
    rows = (
        db.query(ExamSection.exam_id, ExamSection.section_type)
        .filter(ExamSection.section_type.in_(["reading", "listening"]))
        .all()
    )
    out = {}
    for exam_id, stype in rows:
        out.setdefault(exam_id, stype)
    return out


def _cup_reading_listening(db, skill, start, end):
    """{user_id: [top10_count, attempts, score_sum, time_sum]} for one month/skill."""
    lo, hi = (LISTEN_MIN_TIME, LISTEN_MAX_TIME) if skill == "listening" else (MIN_TIME, MAX_TIME)
    skill_map = _exam_skill_map(db)
    rows = (
        db.query(ExamResult.exam_id, ExamResult.user_id, ExamResult.total_score, ExamResult.time_taken)
        .filter(
            ExamResult.is_forecast.in_([False, None]),
            ExamResult.completion_date >= start,
            ExamResult.completion_date < end,
            ExamResult.time_taken >= lo,
            ExamResult.time_taken <= hi,
        )
        .all()
    )
    # Best attempt per (exam, user).
    agg = {}
    for e, u, s, t in rows:
        if e is None or u is None or skill_map.get(e) != skill:
            continue
        s = int(s or 0)
        key = (e, u)
        a = agg.get(key)
        if a is None:
            agg[key] = [s, 1, t]
        else:
            a[1] += 1
            if s > a[0]:
                a[0], a[2] = s, t
            elif s == a[0] and t is not None and (a[2] is None or t < a[2]):
                a[2] = t

    by_exam = {}
    for (e, u), d in agg.items():
        by_exam.setdefault(e, []).append((u, d))

    stats = {}
    for e, entries in by_exam.items():
        entries.sort(key=lambda kv: (-kv[1][0], kv[1][1], kv[1][2] if kv[1][2] is not None else _BIG))
        for u, d in entries[:TOP_N]:
            st = stats.setdefault(u, [0, 0, 0, 0])
            st[0] += 1
            st[1] += d[1]
            st[2] += d[0]
            st[3] += (d[2] or 0)
    return stats


def _cup_writing(db, start, end):
    rows = (
        db.query(
            WritingTask.test_id, WritingTask.part_number,
            WritingAnswer.user_id, WritingAnswer.score, WritingAnswer.time_taken,
        )
        .join(WritingAnswer, WritingAnswer.task_id == WritingTask.task_id)
        .filter(
            WritingAnswer.is_ai_evaluated == True,   # noqa: E712
            WritingAnswer.score > 0,
            WritingAnswer.updated_at >= start,
            WritingAnswer.updated_at < end,
        )
        .all()
    )
    by_eu = {}   # (exam, user) -> {part: (band, time)}
    for test_id, part, uid, score, tt in rows:
        if test_id is None or uid is None or part not in (1, 2):
            continue
        d = by_eu.setdefault((test_id, uid), {})
        s = float(score or 0)
        if part not in d or s > d[part][0]:
            d[part] = (s, tt or 0)

    by_exam = {}
    for (test_id, uid), parts in by_eu.items():
        if 1 in parts and 2 in parts:
            overall = _round_half((parts[1][0] + 2 * parts[2][0]) / 3.0)
            tsum = (parts[1][1] or 0) + (parts[2][1] or 0)
            by_exam.setdefault(test_id, []).append((uid, overall, tsum))

    stats = {}
    for test_id, entries in by_exam.items():
        entries.sort(key=lambda kv: -kv[1])
        for uid, overall, tsum in entries[:TOP_N]:
            st = stats.setdefault(uid, [0, 0, 0, 0])
            st[0] += 1
            st[1] += 1
            st[2] += int(round(overall * 10))
            st[3] += tsum
    return stats


def _cup_speaking(db, start, end):
    """Speaking: only fully graded FULL tests count. Single-part and topic practice
    attempts are excluded — they are shorter and easier to score high on, so mixing
    them into one board would compare different things.

    Speaking has no fixed "test": each attempt is an auto-assembled question set, so
    there is no per-test Top 10 like Reading/Listening/Writing. Instead users are
    ranked by the AVERAGE band of the month's attempts — one more weak attempt pulls
    the rank down, so the board reflects level rather than number of attempts.
    """
    from app.models.models import SpeakingAttempt
    rows = (db.query(SpeakingAttempt.user_id, SpeakingAttempt.overall_band)
            .filter(SpeakingAttempt.test_type == 'full',
                    SpeakingAttempt.submitted.is_(True),
                    SpeakingAttempt.grade_status == 'done',
                    SpeakingAttempt.overall_band.isnot(None),
                    SpeakingAttempt.graded_at >= start,
                    SpeakingAttempt.graded_at < end)
            .all())
    stats = {}
    for uid, band in rows:
        if uid is None:
            continue
        st = stats.setdefault(uid, [0, 0, 0, 0])
        st[0] += 1                               # graded full tests
        st[1] += 1
        st[2] += int(round(float(band) * 10))    # band sum ×10, same unit as Writing
    return stats


def speaking_average(d) -> float:
    """Average band of a Speaking stats row `[tests, attempts, band_sum×10, _]`.

    NOT rounded: this is the RANKING value. Rounding before comparing would turn two
    slightly different users into a tie decided by the secondary key — the opposite
    of "higher average ranks higher". Display code rounds on its own.
    """
    attempts = d[1] or 0
    if not attempts:
        return 0.0
    return d[2] / 10.0 / attempts


def speaking_average_display(d) -> float:
    """Display value rounded to 2 decimals, half-up (3.125 → 3.13, not 3.12 like
    Python's round())."""
    return float(Decimal(str(speaking_average(d))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def compute_cup(db: Session, skill: str, start, end):
    """Ranked list [(user_id, [top10_count, attempts, score, time_sum]), ...]."""
    if skill == "speaking":
        stats = _cup_speaking(db, start, end)
        # Rank by the AVERAGE band of all attempts (27/7 = 3.86 ranks above 25/8 = 3.125);
        # on a tie, whoever took MORE tests ranks higher. Not the shared ordering, since
        # Speaking's first field is "tests taken", not "Top-10 finishes".
        return sorted(stats.items(), key=lambda kv: (-speaking_average(kv[1]), -kv[1][1]))
    stats = (_cup_writing(db, start, end) if skill == "writing"
             else _cup_reading_listening(db, skill, start, end))
    return sorted(
        stats.items(),
        key=lambda kv: (-kv[1][0], kv[1][1], -kv[1][2], kv[1][3] if kv[1][3] else _BIG),
    )


def snapshot_month(db: Session, year: int, month: int) -> dict:
    """Freeze Top 3 per skill for (year, month). Idempotent per skill."""
    start, end = _month_range(year, month)
    out = {}
    for skill in SKILLS:
        if db.query(MonthlyCupWinner).filter_by(skill=skill, year=year, month=month).first():
            out[skill] = "exists"
            continue
        ranked = compute_cup(db, skill, start, end)
        for i, (uid, d) in enumerate(ranked[:TOP3], 1):
            db.add(MonthlyCupWinner(
                skill=skill, year=year, month=month, user_id=uid, rank=i,
                top10_count=d[0], attempts=d[1], score=d[2],
                time_taken=(d[3] or None),
            ))
        out[skill] = len(ranked[:TOP3])
    db.commit()
    return out


def _prev_month():
    now = get_vietnam_time().replace(tzinfo=None)
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


def backfill(db: Session, months: int = 12) -> list:
    """Snapshot the last `months` completed months (idempotent)."""
    now = get_vietnam_time().replace(tzinfo=None)
    y, m = now.year, now.month
    done = []
    for _ in range(months):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        done.append({"year": y, "month": m, "result": snapshot_month(db, y, m)})
    return done


def main():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "backfill":
            n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
            print("Backfill:", backfill(db, n))
        else:
            y, m = _prev_month()
            print(f"Snapshot {m}/{y}:", snapshot_month(db, y, m))
    finally:
        db.close()


if __name__ == "__main__":
    main()
