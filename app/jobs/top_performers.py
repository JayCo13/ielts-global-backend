"""Daily recompute of Top Performer badges + per-skill Hall of Fame totals.

Over ELIGIBLE full-test attempts (time_taken 50–60 min), ALL-TIME, computes each
exam's Top 10 (best attempt per user; score DESC, fewer attempts, faster time —
same order as the live leaderboard), grouped BY SKILL (reading / listening).
Writing is computed later (graded via WritingAnswer, not ExamResult) — its columns
stay 0 for now.

Per user, per skill:
  • {skill}_top10_count  = # distinct exams of that skill they are Top 10 of  → badge tier
  • {skill}_hof_attempts = total attempts across those Top-10 exams
  • {skill}_hof_score    = total best score across those Top-10 exams
Combined: top10_count = read+listen+write, is_top_performer = top10_count > 0.

Badges (per skill: 1 Top / 3 Elite / 10 Master / 25 Legend) and Hall of Fame
(per-skill Top 100) are permanent (never reset). Every run rewrites all fields, so
dropping out of every Top 10 auto-demotes.

Trigger (global has no cron): POST /admin/top-performers/recompute
Or run standalone:            python -m app.jobs.top_performers
"""
from sqlalchemy.orm import Session

from app.models.models import User, ExamResult, ExamSection, WritingTask, WritingAnswer

_BIG = 10 ** 9  # sentinel time for attempts with no recorded time_taken (rank last)
TOP_N = 10
MIN_TIME = 50 * 60   # 3000s — reading eligible lower bound
MAX_TIME = 60 * 60   # 3600s — reading eligible upper bound
LISTEN_MIN_TIME = 20 * 60   # 1200s — listening is shorter (spec: 20–35 min)
LISTEN_MAX_TIME = 35 * 60   # 2100s

# Column-name prefixes per skill.
_PREFIX = {"reading": "read", "listening": "listen", "writing": "write"}


def _exam_skill_map(db: Session) -> dict:
    """exam_id -> 'reading' | 'listening' (first full-test section type on the exam)."""
    rows = (
        db.query(ExamSection.exam_id, ExamSection.section_type)
        .filter(ExamSection.section_type.in_(['reading', 'listening']))
        .all()
    )
    out = {}
    for exam_id, stype in rows:
        out.setdefault(exam_id, stype)   # first wins; an exam is single-skill in practice
    return out


def _stats_for_skill(rows):
    """rows = [(exam_id, user_id, score, time_taken)] for ONE skill →
    {user_id: [top10_count, attempts_sum, score_sum]}."""
    agg = {}  # (exam_id, user_id) -> [best_score, attempts, best_time]
    for exam_id, uid, score, tt in rows:
        score = int(score or 0)
        key = (exam_id, uid)
        a = agg.get(key)
        if a is None:
            agg[key] = [score, 1, tt]
        else:
            a[1] += 1
            if score > a[0]:
                a[0] = score
                a[2] = tt
            elif score == a[0]:
                if tt is not None and (a[2] is None or tt < a[2]):
                    a[2] = tt

    by_exam = {}
    for (exam_id, uid), data in agg.items():
        by_exam.setdefault(exam_id, []).append((uid, data))

    stats = {}  # user_id -> [top10_count, attempts_sum, score_sum]
    for exam_id, entries in by_exam.items():
        entries.sort(key=lambda kv: (-kv[1][0], kv[1][1], kv[1][2] if kv[1][2] is not None else _BIG))
        for uid, data in entries[:TOP_N]:
            s = stats.setdefault(uid, [0, 0, 0])
            s[0] += 1
            s[1] += data[1]
            s[2] += data[0]
    return stats


def _round_half(x: float) -> float:
    """Round to the nearest 0.5 (IELTS band step)."""
    return round(x * 2) / 2.0


def _writing_stats(db: Session):
    """Writing has no ExamResult/time — a WritingAnswer per (task,user) holds the AI
    band. Per writing exam, a user's overall band = (best Task1 + 2*best Task2)/3
    (requires BOTH tasks evaluated), ranked desc; top 10 per exam. Returns
    {user_id: [top10_count, attempts, score_tenths]} where score_tenths sums the
    overall band * 10 (kept integer for the shared Integer column)."""
    rows = (
        db.query(
            WritingTask.test_id, WritingTask.part_number,
            WritingAnswer.user_id, WritingAnswer.score,
        )
        .join(WritingAnswer, WritingAnswer.task_id == WritingTask.task_id)
        .filter(WritingAnswer.is_ai_evaluated == True, WritingAnswer.score > 0)  # noqa: E712
        .all()
    )
    # (exam, user) -> {part_number: best band}
    by_eu = {}
    for test_id, part, uid, score in rows:
        if test_id is None or uid is None or part not in (1, 2):
            continue
        d = by_eu.setdefault((test_id, uid), {})
        s = float(score or 0)
        if part not in d or s > d[part]:
            d[part] = s

    by_exam = {}  # exam -> [(uid, overall_band)]
    for (test_id, uid), parts in by_eu.items():
        if 1 in parts and 2 in parts:  # full-test band needs both tasks
            overall = _round_half((parts[1] + 2 * parts[2]) / 3.0)
            by_exam.setdefault(test_id, []).append((uid, overall))

    stats = {}  # uid -> [top10_count, attempts, score_tenths]
    for test_id, entries in by_exam.items():
        entries.sort(key=lambda kv: -kv[1])
        for uid, overall in entries[:TOP_N]:
            s = stats.setdefault(uid, [0, 0, 0])
            s[0] += 1
            s[1] += 1                      # writing is single-attempt (upsert) → 1/exam
            s[2] += int(round(overall * 10))
    return stats


def recompute_top_performers(db: Session) -> dict:
    skill_map = _exam_skill_map(db)

    rows = (
        db.query(
            ExamResult.exam_id, ExamResult.user_id,
            ExamResult.total_score, ExamResult.time_taken,
        )
        .filter(
            ExamResult.is_forecast.in_([False, None]),
            ExamResult.time_taken >= LISTEN_MIN_TIME,   # broad window covering both skills
            ExamResult.time_taken <= MAX_TIME,
        )
        .all()
    )

    # Split rows by skill, applying the PER-SKILL eligible window
    # (listening 20–35 min, reading 50–60 min).
    rows_by_skill = {"reading": [], "listening": []}
    for exam_id, uid, score, tt in rows:
        if exam_id is None or uid is None:
            continue
        sk = skill_map.get(exam_id)
        if sk not in rows_by_skill:
            continue
        lo, hi = (LISTEN_MIN_TIME, LISTEN_MAX_TIME) if sk == "listening" else (MIN_TIME, MAX_TIME)
        if tt is None or tt < lo or tt > hi:
            continue
        rows_by_skill[sk].append((exam_id, uid, score, tt))

    stats_by_skill = {sk: _stats_for_skill(r) for sk, r in rows_by_skill.items()}
    stats_by_skill["writing"] = _writing_stats(db)

    # Merge into per-user field updates.
    all_uids = set()
    for st in stats_by_skill.values():
        all_uids.update(st.keys())

    # Reset everyone first (only those currently flagged, to keep it cheap).
    reset_fields = {
        User.is_top_performer: False, User.top10_count: 0,
        User.hof_attempts: 0, User.hof_score: 0,
        User.read_top10_count: 0, User.read_hof_attempts: 0, User.read_hof_score: 0,
        User.listen_top10_count: 0, User.listen_hof_attempts: 0, User.listen_hof_score: 0,
        User.write_top10_count: 0, User.write_hof_attempts: 0, User.write_hof_score: 0,
    }
    db.query(User).filter(User.top10_count > 0).update(reset_fields, synchronize_session=False)

    for uid in all_uids:
        rd = stats_by_skill["reading"].get(uid, [0, 0, 0])
        ls = stats_by_skill["listening"].get(uid, [0, 0, 0])
        wr = stats_by_skill["writing"].get(uid, [0, 0, 0])
        total = rd[0] + ls[0] + wr[0]
        db.query(User).filter(User.user_id == uid).update({
            User.read_top10_count: rd[0], User.read_hof_attempts: rd[1], User.read_hof_score: rd[2],
            User.listen_top10_count: ls[0], User.listen_hof_attempts: ls[1], User.listen_hof_score: ls[2],
            User.write_top10_count: wr[0], User.write_hof_attempts: wr[1], User.write_hof_score: wr[2],
            User.top10_count: total, User.is_top_performer: total > 0,
            # keep combined hof_* as a convenience total (reading + listening)
            User.hof_attempts: rd[1] + ls[1], User.hof_score: rd[2] + ls[2],
        }, synchronize_session=False)

    db.commit()
    return {
        "reading_winners": len(stats_by_skill["reading"]),
        "listening_winners": len(stats_by_skill["listening"]),
        "writing_winners": len(stats_by_skill["writing"]),
        "total_users": len(all_uids),
    }


def main():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        print("Top performers:", recompute_top_performers(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
