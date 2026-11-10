"""Batched builder for the student Focus (forecast) lists (2026-11 speed-up).

The original /student/reading/forecasts and /student/listening/forecasts handlers
ran 4–6 TiDB queries PER EXAM (access types, sections, question counts, results,
answer aggregates) and loaded LONGTEXT columns (exam description, section
description / transcript, part_title) they never used. This module produces the
exact same response with:

  * a "static" part that is identical for every student (exams, access types,
    Focus sections, question counts) — 4 batched queries, cached in Redis for
    FORECAST_STATIC_TTL seconds (same pattern as reading-tests:static:v1);
  * a per-student part — 2 or 3 batched queries over all exams at once.

Per-exam semantics are preserved exactly, including listening's fallback to
StudentAnswer for an exam whose results have no ListeningAnswer rows, and the
different "completed" rules (reading: all questions answered; listening: any).
"""
from datetime import datetime

from sqlalchemy.orm import load_only
from sqlalchemy.sql import func

from app.models.models import (
    Exam, ExamSection, ExamAccessType, ExamResult, Question, StudentAnswer, ListeningAnswer,
)
from app.utils.redis_cache import cache

FORECAST_STATIC_TTL = 120  # seconds — admin edits show up within 2 minutes


def _static_key(section_type: str) -> str:
    return f"forecasts:static:{section_type}:v1"


def _load_static(db, section_type: str):
    """[{exam_id, exam_title, access_types, sections:[...]}] in exam_id order,
    only exams that have at least one Focus section."""
    exam_rows = (
        db.query(Exam.exam_id, Exam.title)
        .join(ExamSection, ExamSection.exam_id == Exam.exam_id)
        .filter(Exam.is_active == True, ExamSection.section_type == section_type)  # noqa: E712
        .distinct()
        .order_by(Exam.exam_id)
        .all()
    )
    if not exam_rows:
        return []
    exam_ids = [r[0] for r in exam_rows]

    access = {}
    for eid, atype in (db.query(ExamAccessType.exam_id, ExamAccessType.access_type)
                       .filter(ExamAccessType.exam_id.in_(exam_ids)).all()):
        access.setdefault(eid, []).append(atype)

    sections = (
        db.query(ExamSection)
        .options(load_only(
            ExamSection.section_id, ExamSection.exam_id, ExamSection.order_number,
            ExamSection.is_forecast, ExamSection.occurrence_count, ExamSection.forecast_title,
            ExamSection.is_recommended, ExamSection.question_types, ExamSection.question_type_tags,
            ExamSection.forecast_level, ExamSection.difficulty_label, ExamSection.difficulty_score,
        ))
        .filter(ExamSection.exam_id.in_(exam_ids), ExamSection.section_type == section_type)
        .order_by(ExamSection.exam_id, ExamSection.order_number)
        .all()
    )
    # Manually ticked OR auto-forecast (occurrence >= 1)
    sections = [s for s in sections
                if getattr(s, 'is_forecast', False) or (getattr(s, 'occurrence_count', 0) or 0) >= 1]
    section_ids = [s.section_id for s in sections]

    expected_map = {}
    if section_ids:
        expected_map = dict(
            db.query(Question.section_id, func.count(Question.question_id))
            .filter(Question.section_id.in_(section_ids), Question.question_type != 'main_text')
            .group_by(Question.section_id).all()
        )

    by_exam = {}
    for s in sections:
        by_exam.setdefault(s.exam_id, []).append({
            'section_id': s.section_id,
            'part_number': s.order_number,
            'forecast_title': getattr(s, 'forecast_title', None),
            'total_questions': int(expected_map.get(s.section_id, 0) or 0),
            'is_recommended': bool(getattr(s, 'is_recommended', False)),
            'question_types': getattr(s, 'question_types', None) or getattr(s, 'question_type_tags', None) or [],
            'question_type_tags': s.question_type_tags or [],
            'forecast_level': s.forecast_level,
            'difficulty_label': s.difficulty_label,
            'difficulty_score': s.difficulty_score,
            'occurrence_count': s.occurrence_count or 0,
        })

    return [
        {'exam_id': eid, 'exam_title': title, 'access_types': access.get(eid, []),
         'sections': by_exam[eid]}
        for eid, title in exam_rows if by_exam.get(eid)
    ]


def _aggregate(db, answer_model, res_ids, section_ids):
    return (
        db.query(
            answer_model.result_id,
            Question.section_id,
            func.count(answer_model.answer_id),
            func.coalesce(func.sum(answer_model.score), 0),
        )
        .join(Question, answer_model.question_id == Question.question_id)
        .filter(answer_model.result_id.in_(res_ids), Question.section_id.in_(section_ids))
        .group_by(answer_model.result_id, Question.section_id)
        .all()
    )


def _parse_dt(v):
    if isinstance(v, datetime) or v is None:
        return v
    try:
        return datetime.fromisoformat(v)
    except Exception:
        return None


async def build_forecast_list(db, current_student, section_type: str):
    """Drop-in result for GET /student/{reading|listening}/forecasts."""
    key = _static_key(section_type)
    static = await cache.get(key)
    if not static:
        static = _load_static(db, section_type)
        await cache.set(key, static, ttl=FORECAST_STATIC_TTL)

    allowed_types = []
    if current_student.role == 'student':
        allowed_types = ['student']
    elif current_student.role == 'customer':
        allowed_types = ['no vip', 'vip'] if getattr(current_student, 'is_vip', False) else ['no vip']
    exams = [e for e in static if any(a in allowed_types for a in e['access_types'])]
    if not exams:
        return []

    exam_ids = [e['exam_id'] for e in exams]
    section_ids = [s['section_id'] for e in exams for s in e['sections']]

    # Every result of this student on these exams (with date, to pick the latest attempt).
    results = (
        db.query(ExamResult.result_id, ExamResult.exam_id, ExamResult.completion_date)
        .filter(ExamResult.user_id == current_student.user_id, ExamResult.exam_id.in_(exam_ids))
        .all()
    )
    res_date = {rid: _parse_dt(d) for rid, _eid, d in results}
    res_exam = {rid: eid for rid, eid, _d in results}

    rows = []
    if results:
        res_ids = list(res_date.keys())
        if section_type == 'listening':
            rows = list(_aggregate(db, ListeningAnswer, res_ids, section_ids))
            # Per-exam fallback (as before): an exam whose results have no
            # ListeningAnswer rows at all is read from StudentAnswer instead.
            exams_with_listen = {res_exam[r[0]] for r in rows}
            fallback_ids = [rid for rid, eid in res_exam.items() if eid not in exams_with_listen]
            if fallback_ids:
                rows += list(_aggregate(db, StudentAnswer, fallback_ids, section_ids))
        else:
            rows = list(_aggregate(db, StudentAnswer, res_ids, section_ids))

    attempts_by_section = {}
    earned_by_rs = {}
    for rid, sid, cnt, earned in rows:
        attempts_by_section.setdefault(sid, []).append((rid, cnt))
        earned_by_rs[(rid, sid)] = int(earned or 0)

    out = []
    for e in exams:
        parts = []
        for s in e['sections']:
            sid = s['section_id']
            expected = s['total_questions']
            candidates = attempts_by_section.get(sid, [])
            if section_type == 'listening':
                complete = [(rid, cnt) for rid, cnt in candidates if cnt > 0]
            else:
                complete = [(rid, cnt) for rid, cnt in candidates if cnt == expected]
            attempts_count = len(complete)
            latest_score = None
            if complete:
                latest_rid = max(complete, key=lambda rc: (res_date.get(rc[0]) or datetime.min))[0]
                latest_score = earned_by_rs.get((latest_rid, sid), 0)
            parts.append({
                'part_number': s['part_number'],
                'forecast_title': s['forecast_title'],
                'completed': attempts_count > 0,
                'attempts_count': attempts_count,
                'latest_score': latest_score,
                'total_questions': expected,
                'is_recommended': s['is_recommended'],
                'question_types': s['question_types'],
                'question_type_tags': s['question_type_tags'],
                'forecast_level': s['forecast_level'],
                'difficulty_label': s['difficulty_label'],
                'difficulty_score': s['difficulty_score'],
                'occurrence_count': s['occurrence_count'],
            })
        out.append({'exam_id': e['exam_id'], 'exam_title': e['exam_title'], 'parts': parts})
    return out


async def build_public_forecast_list(db, section_type: str):
    """Drop-in result for GET /public/{reading|listening}-forecasts (guests):
    every exam with Focus parts, no access filter, no per-user data."""
    key = _static_key(section_type)
    static = await cache.get(key)
    if not static:
        static = _load_static(db, section_type)
        await cache.set(key, static, ttl=FORECAST_STATIC_TTL)
    return [
        {
            'exam_id': e['exam_id'],
            'exam_title': e['exam_title'],
            'parts': [{
                'part_number': s['part_number'],
                'forecast_title': s['forecast_title'],
                'completed': False,
                'attempts_count': 0,
                'is_recommended': s['is_recommended'],
                'question_types': s['question_types'],
                'difficulty_label': s['difficulty_label'],
                'forecast_level': s['forecast_level'],
            } for s in e['sections']],
        }
        for e in static
    ]
