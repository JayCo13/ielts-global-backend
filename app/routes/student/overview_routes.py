"""Student "Results Overview" — a per-student overview across ALL their attempts:
4 skills (reading/listening/writing/speaking) with average, trend and a progress
series, plus a per-question-type breakdown (accuracy + trend) for reading/listening.

Ported from VN. The aggregation helpers VN shares with its teacher dashboard live in
app/utils/result_stats.py in the global stack (global has no center/teacher module).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, case

from app.database import get_db
from app.models.models import (
    User, ExamResult, StudentAnswer, ListeningAnswer, Question,
    WritingAnswer, WritingTask,
)
from app.routes.student.student_actions import get_current_student
from app.utils.result_stats import (
    build_skill_history, _exam_skill_map, _student_accuracy, _accuracy,
)

router = APIRouter()


def _type_acc(db: Session, model, result_ids):
    """{question_type: (total, correct)} for the given answer model + result ids."""
    if not result_ids:
        return {}
    cat = func.coalesce(Question.stats_category, Question.question_type)
    rows = (
        db.query(
            cat,
            func.count(model.answer_id),
            func.sum(case((model.score > 0, 1), else_=0)),
        )
        .join(model, model.question_id == Question.question_id)
        .filter(model.result_id.in_(result_ids), Question.question_type != 'main_text')
        .group_by(cat)
        .all()
    )
    return {qt: (int(t or 0), int(c or 0)) for qt, t, c in rows if qt}


def _type_stats_with_trend(db: Session, user_id: int) -> dict:
    """Per-question-type accuracy + trend (recent half vs older half) for R/L."""
    results = (
        db.query(ExamResult.result_id, ExamResult.exam_id)
        .filter(ExamResult.user_id == user_id)
        .order_by(ExamResult.completion_date.asc())
        .all()
    )
    exam_ids = list({r.exam_id for r in results if r.exam_id})
    skill_map = _exam_skill_map(db, exam_ids)

    ids_by_skill = {"reading": [], "listening": []}
    for r in results:
        sk = skill_map.get(r.exam_id)
        if sk in ids_by_skill:
            ids_by_skill[sk].append(r.result_id)

    model_for = {"reading": StudentAnswer, "listening": ListeningAnswer}
    out = {"reading": [], "listening": []}
    for sk, ids in ids_by_skill.items():
        model = model_for[sk]
        overall = _type_acc(db, model, ids)
        mid = len(ids) // 2
        older = _type_acc(db, model, ids[:mid]) if mid else {}
        recent = _type_acc(db, model, ids[mid:]) if mid else {}
        rows = []
        for qt, (t, c) in overall.items():
            trend = None
            if qt in older and qt in recent and older[qt][0] and recent[qt][0]:
                ao = older[qt][1] / older[qt][0] * 100
                ar = recent[qt][1] / recent[qt][0] * 100
                trend = round(ar - ao, 1)
            rows.append({
                "type": qt,
                "total": t,
                "correct": c,
                "accuracy": _accuracy(c, t),
                "trend": trend,
            })
        rows.sort(key=lambda x: x["total"], reverse=True)
        out[sk] = rows
    return out


def _wb_agg(vals):
    """{count, average band, trend} for a chronological (oldest-first) band list.
    Trend = recent-half average minus older-half average (same convention as the
    reading/listening per-type trend)."""
    n = len(vals)
    if not n:
        return {"count": 0, "average": None, "trend": None}
    avg = round(sum(vals) / n, 1)
    trend = None
    if n >= 2:
        mid = n // 2
        older, recent = vals[:mid], vals[mid:]
        if older and recent:
            trend = round(sum(recent) / len(recent) - sum(older) / len(older), 1)
    return {"count": n, "average": avg, "trend": trend}


def _writing_breakdown(db: Session, user_id: int) -> dict:
    """Writing overview split by Task 1 / Task 2 (average band + trend) and by
    IELTS question type (WritingTask.question_type_tags, set in the forecast/authoring
    tools). Each essay's band contributes to its task and to every tag it carries."""
    rows = (
        db.query(
            WritingAnswer.score,
            WritingTask.part_number,
            WritingTask.question_type_tags,
            WritingAnswer.task_achievement_score,
            WritingAnswer.coherence_cohesion_score,
            WritingAnswer.lexical_resource_score,
            WritingAnswer.grammatical_range_score,
        )
        .join(WritingTask, WritingTask.task_id == WritingAnswer.task_id)
        .filter(WritingAnswer.user_id == user_id, WritingAnswer.score.isnot(None))
        .order_by(WritingAnswer.created_at.asc())
        .all()
    )
    tasks = {1: [], 2: []}
    types = {}
    crit = {"tr": [], "cc": [], "lr": [], "gra": []}
    for score, part, tags, tr, cc, lr, gra in rows:
        sc = float(score)
        if part in (1, 2):
            tasks[part].append(sc)
        if isinstance(tags, list):
            for tag in tags:
                if tag:
                    types.setdefault(str(tag), []).append(sc)
        for key, val in (("tr", tr), ("cc", cc), ("lr", lr), ("gra", gra)):
            if val is not None:
                crit[key].append(float(val))

    task_rows = []
    for p in (1, 2):
        agg = _wb_agg(tasks[p])
        if agg["count"]:
            task_rows.append({"part": p, "label": f"Task {p}", **agg})
    type_rows = [{"type": t, **_wb_agg(v)} for t, v in types.items()]
    type_rows.sort(key=lambda x: x["count"], reverse=True)
    # Average component scores (TR/CC/LR/GRA) across all evaluated essays.
    criteria = {k: (round(sum(v) / len(v), 1) if v else None) for k, v in crit.items()}
    return {"tasks": task_rows, "types": type_rows, "criteria": criteria}


def _speaking_breakdown(db: Session, user_id: int) -> dict:
    """Speaking overview: split by Part 1 / 2 / 3, with average band and the 4 criteria.

    Source is `SpeakingAttempt.part_results` — the grade frozen at submit time; nothing
    is recomputed here. Only fully graded attempts count; abandoned or ungraded ones
    have no score and would only dilute the numbers.

    Part Band and Overall are different things: Overall comes from each part's four
    criterion scores, not from averaging Part Bands — so both are returned and neither
    is derived from the other.
    """
    from app.models.models import SpeakingAttempt

    rows = (db.query(SpeakingAttempt.part_results, SpeakingAttempt.criteria,
                     SpeakingAttempt.overall_band, SpeakingAttempt.started_at)
            .filter(SpeakingAttempt.user_id == user_id,
                    SpeakingAttempt.grade_status == 'done')
            .order_by(SpeakingAttempt.started_at.asc())
            .all())

    parts = {'Part 1': [], 'Part 2': [], 'Part 3': []}
    crit = {'pronunciation': [], 'fluency_coherence': [],
            'lexical_resource': [], 'grammar': []}
    overalls = []
    for part_results, criteria, overall, _started in rows:
        for p in (part_results or []):
            if not isinstance(p, dict):
                continue
            band = p.get('band')
            if p.get('label') in parts and band is not None:
                try:
                    parts[p['label']].append(float(band))
                except (TypeError, ValueError):
                    pass
        for key, bucket in crit.items():
            v = (criteria or {}).get(key)
            if v is not None:
                try:
                    bucket.append(float(v))
                except (TypeError, ValueError):
                    pass
        if overall is not None:
            try:
                overalls.append(float(overall))
            except (TypeError, ValueError):
                pass

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    return {
        "attempts": len(rows),
        "overall_avg": avg(overalls),
        # Latest overall band, for a trend against the average (same as other skills).
        "latest": overalls[-1] if overalls else None,
        "parts": [{"label": k, "avg": avg(v), "count": len(v)} for k, v in parts.items()],
        "criteria": [
            {"key": "fluency_coherence", "label": "Fluency & Coherence", "avg": avg(crit['fluency_coherence'])},
            {"key": "lexical_resource", "label": "Lexical Resource", "avg": avg(crit['lexical_resource'])},
            {"key": "grammar", "label": "Grammatical Range & Accuracy", "avg": avg(crit['grammar'])},
            {"key": "pronunciation", "label": "Pronunciation", "avg": avg(crit['pronunciation'])},
        ],
    }


@router.get("/results-overview")
async def results_overview(
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    uid = current_student.user_id
    return {
        "overall": _student_accuracy(db, uid),
        "skills": build_skill_history(db, uid),
        "question_type_stats": _type_stats_with_trend(db, uid),
        "writing_breakdown": _writing_breakdown(db, uid),
        "speaking_breakdown": _speaking_breakdown(db, uid),
    }
