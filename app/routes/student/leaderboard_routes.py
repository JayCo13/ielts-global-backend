"""Leaderboard + Monthly Cup + Hall of Fame (ported from VN).

Monthly leaderboard for a full-test exam: only ELIGIBLE submits (time_taken 50–60
min) of the CURRENT month count, top 10 by score (best attempt) with ties broken by
fewer attempts then faster time, plus the caller's own rank X/Y (or an
ineligibility message). Resets automatically at month start (it's just a date
filter). Hall of Fame is the permanent, never-reset cumulative ranking.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import User, ExamResult, ExamSection, MonthlyCupWinner
from app.routes.student.student_actions import get_current_student
from app.utils.datetime_utils import get_vietnam_time
from app.jobs.monthly_cup import (compute_cup, _month_range, speaking_average,
                                  speaking_average_display)

router = APIRouter()

_BIG = 10 ** 9          # sentinel time for attempts with no recorded time_taken
MIN_TIME = 50 * 60      # 3000s — reading/writing eligible lower bound
MAX_TIME = 60 * 60      # 3600s — reading/writing eligible upper bound
LISTEN_MIN_TIME = 20 * 60   # 1200s — listening tests are shorter (spec: 20–35 min)
LISTEN_MAX_TIME = 35 * 60   # 2100s


def _time_window(skill):
    """Eligible attempt duration per skill: listening 20–35 min, reading/writing 50–60 min."""
    if skill == 'listening':
        return LISTEN_MIN_TIME, LISTEN_MAX_TIME
    return MIN_TIME, MAX_TIME


def _ineligible_msg(skill):
    lo, hi = (20, 35) if skill == 'listening' else (50, 60)
    return ("Your attempt is not eligible for the ranking because the time taken "
            f"was not between {lo} and {hi} minutes.")


def _month_bounds():
    now = get_vietnam_time().replace(tzinfo=None)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = start.replace(year=now.year + 1, month=1) if now.month == 12 \
        else start.replace(month=now.month + 1)
    return start, nxt, now.month, now.year


@router.get("/leaderboard/{exam_id}")
async def leaderboard(
    exam_id: int,
    limit: int = 10,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    # The exam-room dialog wants a compact Top 10; the result page paginates the full
    # ranking (limit up to 500). Clamp to a sane range.
    limit = max(1, min(int(limit or 10), 500))
    month_start, month_end, month, year = _month_bounds()

    # This exam's skill → show each user's per-skill badge tier in the board.
    sec = (
        db.query(ExamSection.section_type)
        .filter(ExamSection.exam_id == exam_id,
                ExamSection.section_type.in_(['reading', 'listening']))
        .first()
    )
    exam_skill = sec[0] if sec else 'reading'
    min_t, max_t = _time_window(exam_skill)

    def _skill_count(u):
        if not u:
            return 0
        return int((u.read_top10_count if exam_skill == 'reading' else u.listen_top10_count) or 0)

    rows = (
        db.query(ExamResult.user_id, ExamResult.total_score, ExamResult.time_taken)
        .filter(
            ExamResult.exam_id == exam_id,
            ExamResult.is_forecast.in_([False, None]),        # full test only
            ExamResult.time_taken >= min_t,                   # per-skill eligibility window
            ExamResult.time_taken <= max_t,
            ExamResult.completion_date >= month_start,        # current month only
            ExamResult.completion_date < month_end,
        )
        .all()
    )

    # Per user: best score, attempts, fastest time among best-score attempts.
    agg = {}  # user_id -> [best_score, attempts, best_time]
    for uid, score, tt in rows:
        score = int(score or 0)
        a = agg.get(uid)
        if a is None:
            agg[uid] = [score, 1, tt]
        else:
            a[1] += 1
            if score > a[0]:
                a[0] = score
                a[2] = tt
            elif score == a[0]:
                if tt is not None and (a[2] is None or tt < a[2]):
                    a[2] = tt

    ranked = sorted(
        agg.items(),
        key=lambda kv: (-kv[1][0], kv[1][1], kv[1][2] if kv[1][2] is not None else _BIG),
    )
    total = len(ranked)

    my_id = current_student.user_id
    my_rank = next((i for i, (uid, _) in enumerate(ranked, 1) if uid == my_id), None)

    top = ranked[:limit]
    need_ids = {uid for uid, _ in top}
    if my_id in agg:
        need_ids.add(my_id)
    users = (
        {u.user_id: u for u in db.query(User).filter(User.user_id.in_(need_ids)).all()}
        if need_ids else {}
    )

    def entry(rank, uid, data):
        u = users.get(uid)
        return {
            "rank": rank,
            "user_id": uid,
            "name": (u.username if u and u.username else None) or "Anonymous",
            "avatar": u.image_url if u else None,
            "score": data[0],
            "attempts": data[1],
            "time_taken": data[2],
            "is_me": uid == my_id,
            # Badge reflects THIS exam's skill tier (per-skill Top-10 count).
            "is_top_performer": _skill_count(u) > 0,
            "top10_count": _skill_count(u),
        }

    top_list = [entry(i, uid, data) for i, (uid, data) in enumerate(top, 1)]
    me = entry(my_rank, my_id, agg[my_id]) if (my_rank and my_id in agg) else None

    # If the caller submitted this month but isn't ranked, it's because no submit fell
    # in the 50–60 min window → show the ineligibility message instead of a rank.
    me_message = None
    if me is None:
        attempted = (
            db.query(ExamResult.result_id)
            .filter(
                ExamResult.exam_id == exam_id,
                ExamResult.user_id == my_id,
                ExamResult.is_forecast.in_([False, None]),
                ExamResult.completion_date >= month_start,
                ExamResult.completion_date < month_end,
            )
            .first()
        )
        if attempted is not None:
            me_message = _ineligible_msg(exam_skill)

    return {
        "total": total,
        "month": f"{month}/{year}",
        "top": top_list,
        "me": me,
        "me_message": me_message,
    }


_SKILLS = ("reading", "listening", "writing", "speaking")


@router.get("/monthly-cup")
async def monthly_cup(
    skill: str = "reading",
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """Monthly Cup — per-skill ranking aggregated over the CURRENT month's eligible
    attempts (resets each month; a date filter, like the per-test board). Ranked by
    # tests Top-10 (desc), fewer attempts, higher score, faster time. Top 100 + my rank."""
    if skill not in _SKILLS:
        skill = "reading"
    now = get_vietnam_time().replace(tzinfo=None)
    start, end = _month_range(now.year, now.month)
    ranked = compute_cup(db, skill, start, end)

    my_id = current_student.user_id
    my_rank = next((i for i, (uid, _) in enumerate(ranked, 1) if uid == my_id), None)

    top = ranked[:100]
    uids = [uid for uid, _ in top]
    if my_id not in uids and my_rank:
        uids.append(my_id)
    users = ({u.user_id: u for u in db.query(User).filter(User.user_id.in_(uids)).all()}
             if uids else {})

    def _score(d):
        # Speaking ranks by AVERAGE band, so show the exact ranking value — showing the
        # band sum would not let students reconcile the order.
        if skill == "speaking":
            return speaking_average_display(d)
        # Writing is band-scored, stored ×10 to keep an integer.
        return round(d[2] / 10.0, 1) if skill == "writing" else int(d[2])

    def entry(rank, uid, d):
        u = users.get(uid)
        return {
            "rank": rank,
            "user_id": uid,
            "name": (u.username if u and u.username else None) or "Anonymous",
            "avatar": u.image_url if u else None,
            "top10_count": d[0],
            "attempts": d[1],
            "score": _score(d),
            "time_taken": d[3] or None,
            # Speaking has no fixed test, so no per-test Top 10: the first field is the
            # NUMBER of graded full tests. The UI relabels based on this flag.
            "count_label": "tests" if skill == "speaking" else "top10",
            # 'band_avg' = AVERAGE band (Speaking), 'band' = band sum (Writing).
            "score_unit": ("band_avg" if skill == "speaking"
                           else "band" if skill == "writing" else "correct"),
            "is_me": uid == my_id,
        }

    entries = [entry(i, uid, d) for i, (uid, d) in enumerate(top, 1)]
    me = None
    if my_rank and my_rank > 100:
        md = dict(ranked)[my_id]
        me = entry(my_rank, my_id, md)
    return {"skill": skill, "month": f"{now.month}/{now.year}", "entries": entries, "me": me}


@router.get("/hall-of-fame")
async def hall_of_fame(
    skill: str = "reading",
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """Hall of Fame — accumulated across all past monthly cups. Ranked by number of
    Top-3 finishes in the Monthly Cup (desc), then total score (desc), then total time (asc).
    Aggregated from `monthly_cup_winners` (frozen at each month end)."""
    if skill not in _SKILLS:
        skill = "reading"
    my_id = current_student.user_id
    rows = (
        db.query(
            MonthlyCupWinner.user_id,
            func.count(MonthlyCupWinner.winner_id).label("top3"),
            func.coalesce(func.sum(MonthlyCupWinner.score), 0).label("score"),
            func.coalesce(func.sum(MonthlyCupWinner.time_taken), 0).label("time"),
            func.coalesce(func.sum(MonthlyCupWinner.attempts), 0).label("attempts"),
        )
        .filter(MonthlyCupWinner.skill == skill)
        .group_by(MonthlyCupWinner.user_id)
        .all()
    )
    ranked = sorted(rows, key=lambda r: (-int(r.top3), -int(r.score or 0), int(r.time or 0) or _BIG))[:100]
    uids = [r.user_id for r in ranked]
    users = ({u.user_id: u for u in db.query(User).filter(User.user_id.in_(uids)).all()}
             if uids else {})

    def _score(raw, attempts=0):
        # Speaking shows the AVERAGE band, same unit as the Monthly Cup.
        if skill == "speaking":
            return speaking_average_display([0, attempts, int(raw or 0), 0])
        return round(int(raw or 0) / 10.0, 1) if skill == "writing" else int(raw or 0)

    entries = []
    for i, r in enumerate(ranked, 1):
        u = users.get(r.user_id)
        entries.append({
            "rank": i,
            "user_id": r.user_id,
            "name": (u.username if u and u.username else None) or "Anonymous",
            "avatar": u.image_url if u else None,
            "cup_top3": int(r.top3),
            "score": _score(r.score, int(r.attempts or 0)),
            "total_time": int(r.time or 0) or None,
            "score_unit": ("band_avg" if skill == "speaking"
                           else "band" if skill == "writing" else "correct"),
            "is_me": r.user_id == my_id,
        })
    return {"skill": skill, "entries": entries}
