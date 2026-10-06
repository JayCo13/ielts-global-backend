"""Per-student result aggregation helpers (ported from the VN teacher/center dashboard).

Global has no center/teacher dashboard, so the helpers the student "Results Overview"
needs live here instead of in routes/center/teacher_dashboard.py. Output shapes are
identical to VN's; per-result accuracy and writing-task lookups are batched (VN ran
one query per result/answer).
"""
from typing import List

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.models.models import (
    ExamResult, StudentAnswer, ListeningAnswer, Exam, ExamSection,
    WritingAnswer, WritingTask,
)


def _accuracy(correct: int, total: int) -> float:
    return round(correct / total * 100, 1) if total else 0.0


def _student_accuracy(db: Session, user_id: int) -> dict:
    """Overall accuracy across a student's answers (reading via StudentAnswer joined
    through ExamResult, plus ListeningAnswer)."""
    sa_total = (
        db.query(func.count(StudentAnswer.answer_id))
        .join(ExamResult, StudentAnswer.result_id == ExamResult.result_id)
        .filter(ExamResult.user_id == user_id).scalar() or 0
    )
    sa_correct = (
        db.query(func.count(StudentAnswer.answer_id))
        .join(ExamResult, StudentAnswer.result_id == ExamResult.result_id)
        .filter(ExamResult.user_id == user_id, StudentAnswer.score > 0).scalar() or 0
    )
    la_total = db.query(func.count(ListeningAnswer.answer_id)).filter(
        ListeningAnswer.user_id == user_id).scalar() or 0
    la_correct = db.query(func.count(ListeningAnswer.answer_id)).filter(
        ListeningAnswer.user_id == user_id, ListeningAnswer.score > 0).scalar() or 0
    total = sa_total + la_total
    correct = sa_correct + la_correct
    return {"accuracy": _accuracy(correct, total), "answered": total}


def _results_accuracy(db: Session, result_ids: List[int]) -> dict:
    """{result_id: accuracy %} over StudentAnswer + ListeningAnswer rows, batched."""
    if not result_ids:
        return {}
    counts = {}
    for model in (StudentAnswer, ListeningAnswer):
        rows = (
            db.query(
                model.result_id,
                func.count(model.answer_id),
                func.sum(case((model.score > 0, 1), else_=0)),
            )
            .filter(model.result_id.in_(result_ids))
            .group_by(model.result_id)
            .all()
        )
        for rid, total, correct in rows:
            t, c = counts.get(rid, (0, 0))
            counts[rid] = (t + int(total or 0), c + int(correct or 0))
    return {rid: _accuracy(counts.get(rid, (0, 0))[1], counts.get(rid, (0, 0))[0])
            for rid in result_ids}


def _exam_skill_map(db: Session, exam_ids: List[int]) -> dict:
    """exam_id -> 'listening'/'reading'/'writing'/'speaking' from section_type."""
    if not exam_ids:
        return {}
    rows = db.query(ExamSection.exam_id, ExamSection.section_type).filter(
        ExamSection.exam_id.in_(exam_ids)).all()
    out = {}
    for eid, st in rows:
        if eid in out:
            continue
        s = (st or "").lower()
        # Writing exams store section_type='essay'.
        for token, sk in (("listening", "listening"), ("reading", "reading"),
                          ("essay", "writing"), ("writing", "writing"), ("speaking", "speaking")):
            if token in s:
                out[eid] = sk
                break
    return out


def build_skill_history(db: Session, user_id: int) -> dict:
    """Per-skill history: Listening/Reading (accuracy from ExamResult), Writing (AI band
    from WritingAnswer), Speaking (band from graded SpeakingAttempt). Each skill has an
    average, a trend (latest vs previous) and the item list (newest first)."""
    skills = {
        "listening": {"unit": "accuracy", "items": []},
        "reading": {"unit": "accuracy", "items": []},
        "writing": {"unit": "band", "items": []},
        "speaking": {"unit": "band", "items": []},
    }

    # Reading / Listening — ExamResult (oldest first for trend calc).
    results = (db.query(ExamResult).filter(ExamResult.user_id == user_id)
               .order_by(ExamResult.completion_date.asc()).all())
    exam_ids = list({r.exam_id for r in results if r.exam_id})
    skill_map = _exam_skill_map(db, exam_ids)
    titles = {e.exam_id: e.title for e in
              db.query(Exam).filter(Exam.exam_id.in_(exam_ids)).all()} if exam_ids else {}
    rl_results = [r for r in results if skill_map.get(r.exam_id, "reading") in ("listening", "reading")]
    acc = _results_accuracy(db, [r.result_id for r in rl_results])
    for r in rl_results:
        sk = skill_map.get(r.exam_id, "reading")
        skills[sk]["items"].append({
            "result_id": r.result_id,
            "title": titles.get(r.exam_id),
            "value": acc.get(r.result_id, 0.0),   # accuracy %
            "score": r.total_score,
            "date": r.completion_date.isoformat() if r.completion_date else None,
            "is_forecast": r.is_forecast,
            "part": r.forecast_part,
        })

    # Writing — WritingAnswer AI band (oldest first).
    wrows = (db.query(WritingAnswer).filter(WritingAnswer.user_id == user_id)
             .order_by(WritingAnswer.created_at.asc()).all())
    task_ids = list({w.task_id for w in wrows if w.task_id})
    tasks = {t.task_id: t for t in
             db.query(WritingTask.task_id, WritingTask.title, WritingTask.part_number)
             .filter(WritingTask.task_id.in_(task_ids)).all()} if task_ids else {}
    for w in wrows:
        task = tasks.get(w.task_id)
        skills["writing"]["items"].append({
            "answer_id": w.answer_id,
            "title": (task.title if task and task.title else None) or f"Writing Task {w.task_id}",
            "value": w.score,        # AI band 0-9
            "score": w.score,
            "part": task.part_number if task else None,
            "date": w.created_at.isoformat() if w.created_at else None,
            "is_ai": bool(w.is_ai_evaluated),
        })

    # Speaking — only fully graded attempts. A single-part attempt deliberately has no
    # Overall, so use that part's band (same rule as the history's display_band).
    from app.models.models import SpeakingAttempt
    srows = (db.query(SpeakingAttempt)
             .filter(SpeakingAttempt.user_id == user_id,
                     SpeakingAttempt.grade_status == 'done')
             .order_by(SpeakingAttempt.started_at.asc()).all())
    TYPE_LABEL = {'full': 'Speaking Full Test', 'part1': 'Speaking Part 1',
                  'part2': 'Speaking Part 2', 'part3': 'Speaking Part 3'}
    for a in srows:
        band = a.overall_band
        if band is None:
            bands = [p.get('band') for p in (a.part_results or [])
                     if isinstance(p, dict) and p.get('band') is not None]
            band = bands[0] if len(bands) == 1 else None
        plan = a.plan or {}
        skills["speaking"]["items"].append({
            "attempt_id": a.attempt_id,
            "title": plan.get('forecast_topic_title') or TYPE_LABEL.get(a.test_type, 'Speaking'),
            "value": band,
            "score": band,
            "part": None if a.test_type == 'full' else a.test_type,
            "date": a.started_at.isoformat() if a.started_at else None,
            "is_forecast": bool(plan.get('forecast_topic_id')),
        })

    # Average + trend, then present newest-first.
    for name, sd in skills.items():
        vals = [it["value"] for it in sd["items"] if it.get("value") is not None]
        sd["count"] = len(sd["items"])
        if name == "writing":
            # Weighted band: Task 2 counts double → (Task1 + Task2×2)/3 style.
            num = den = 0
            for it in sd["items"]:
                if it.get("value") is None:
                    continue
                wt = 2 if it.get("part") == 2 else 1
                num += it["value"] * wt
                den += wt
            sd["average"] = round(num / den, 1) if den else None
        else:
            sd["average"] = round(sum(vals) / len(vals), 1) if vals else None
        sd["trend"] = round(vals[-1] - vals[-2], 1) if len(vals) >= 2 else None
        sd["items"] = list(reversed(sd["items"]))
    return skills
