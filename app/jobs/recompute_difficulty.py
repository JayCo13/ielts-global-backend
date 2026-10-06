"""Daily difficulty recompute for Reading/Listening parts, Writing tasks and
Speaking topics.

Trigger (global has no cron): POST /admin/difficulty/recompute
Or run standalone:            python -m app.jobs.recompute_difficulty

Per part (reading/listening ExamSection) / task (writing WritingTask):
  • collect scores over VALID attempts, store the average + valid count
  • a part/task is "classified" once it has >= MIN_VALID valid attempts
  • when >= MIN_CLASSIFIED items of a skill are classified, assign percentile
    labels (top 35% avg → 'easy' … bottom 15% → 'very_hard'); otherwise no label

Validity (per spec):
  • Reading/Listening: attempt score >= VALID_MIN_PCT (40%) of the part AND no more
    than MAX_BLANKS (2) answers left blank — filters out "just peeking" / random picks.
  • Writing: band >= WRITING_MIN_BAND and word count >= 120 (Task 1) / 150 (Task 2)
"""
from sqlalchemy import func, text, or_
from sqlalchemy.orm import Session

from app.models.models import (
    ExamSection, ExamResult, WritingTask, WritingAnswer, Question,
    StudentAnswer, ListeningAnswer,
)
from app.utils.datetime_utils import get_vietnam_time

MIN_VALID = 30           # valid attempts before a part/task is classified
MIN_CLASSIFIED = 20      # classified items of a skill before percentile runs
# Lowered from 100/50: writing has far fewer attempts per task than reading, so at
# 100/50 no writing task ever classified (0 labels). At 30/20, ~27 writing tasks
# and ~337 reading parts classify — enough for a stable percentile, and difficulty
# finally shows for writing as well as reading.
VALID_MIN_PCT = 40.0     # reading/listening: attempt must reach this % of the part
MAX_BLANKS = 2           # …and leave no more than this many answers blank
WRITING_MIN_BAND = 4.0
# Speaking: a "valid attempt" is an answer the student REALLY spoke, not a blank or a
# two-second mumble. No band threshold like Writing: low Speaking bands are normal, and
# filtering by band would discard exactly the weaker students a hard topic affects most.
SPEAKING_MIN_MS = 5000
WRITING_MIN_WORDS = {1: 120, 2: 150}

LABELS = ('easy', 'medium', 'hard', 'very_hard')


def _assign_labels(all_items, classified):
    """Reset every item's label, then percentile-label the classified pool (if big
    enough). classified must be pre-filtered to items with a difficulty_score."""
    for it in all_items:
        it.difficulty_label = None
    if len(classified) < MIN_CLASSIFIED:
        return
    ranked = sorted(classified, key=lambda x: -(x.difficulty_score or 0))
    n = len(ranked)
    # Percentile split (per updated spec): top 35% → Easy, 25% → Medium,
    # 25% → Hard, last 15% → Very hard.
    for i, it in enumerate(ranked):
        q = i / n
        it.difficulty_label = (
            'easy' if q < 0.35 else 'medium' if q < 0.60 else 'hard' if q < 0.85 else 'very_hard'
        )


def _recompute_sections(db: Session, now, stype: str):
    """Recompute difficulty for one section skill ('reading' or 'listening').
    Both store per-section {earned,total} in ExamResult.section_scores identically,
    so the logic is shared; each skill is classified independently."""
    sections = db.query(ExamSection).filter(ExamSection.section_type == stype).all()
    if not sections:
        return 0
    sec_ids = [s.section_id for s in sections]
    qcounts = dict(
        db.query(Question.section_id, func.count(Question.question_id))
        .filter(Question.section_id.in_(sec_ids), Question.question_type != 'main_text')
        .group_by(Question.section_id)
        .all()
    )

    # Blank answers per (result_id, section_id): a submit writes a row for EVERY
    # question in range, so an unanswered one is a row with empty student_answer.
    # A valid attempt must leave <= MAX_BLANKS blank (spec: "no more than 2 left blank").
    ans_model = StudentAnswer if stype == 'reading' else ListeningAnswer
    blank_map = {}
    for rid, sid, cnt in (
        db.query(ans_model.result_id, Question.section_id, func.count(ans_model.answer_id))
        .join(Question, ans_model.question_id == Question.question_id)
        .filter(
            Question.section_id.in_(sec_ids),
            or_(ans_model.student_answer.is_(None), func.trim(ans_model.student_answer) == ''),
        )
        .group_by(ans_model.result_id, Question.section_id)
        .all()
    ):
        blank_map[(rid, sid)] = cnt

    by_exam = {}
    for s in sections:
        by_exam.setdefault(s.exam_id, []).append(s)

    for exam_id, secs in by_exam.items():
        results = (
            db.query(
                ExamResult.result_id, ExamResult.section_scores, ExamResult.is_forecast,
                ExamResult.forecast_part, ExamResult.total_score,
            )
            .filter(ExamResult.exam_id == exam_id)
            .all()
        )
        for s in secs:
            ptotal = qcounts.get(s.section_id, 0) or 0
            pcts = []
            for rid, ss_json, is_fc, fp, tot in results:
                pct = None
                if is_fc:
                    if fp == s.order_number and ptotal > 0:
                        pct = (tot or 0) / ptotal * 100.0
                else:
                    ss = ss_json or {}
                    cell = ss.get(str(s.section_id)) or ss.get(s.section_id)
                    if isinstance(cell, dict) and cell.get('total'):
                        pct = (cell.get('earned', 0) or 0) / cell['total'] * 100.0
                # Valid = reached VALID_MIN_PCT of the part AND left <= MAX_BLANKS blank.
                blanks = blank_map.get((rid, s.section_id), 0)
                if pct is not None and pct >= VALID_MIN_PCT and blanks <= MAX_BLANKS:
                    pcts.append(pct)
            s.difficulty_valid_count = len(pcts)
            s.difficulty_score = round(sum(pcts) / len(pcts), 1) if pcts else None
            s.difficulty_updated_at = now

    classified = [s for s in sections
                  if (s.difficulty_valid_count or 0) >= MIN_VALID and s.difficulty_score is not None]
    _assign_labels(sections, classified)
    return len(classified)


def _recompute_speaking(db: Session, now):
    """Difficulty of a Speaking topic = the average band students achieve on it.

    The unit is the TOPIC, not the question: students pick a topic to practise, and a
    single question rarely has enough attempts to rank stably. Higher band = easier
    topic — same direction as Writing, so `_assign_labels` is shared.
    """
    from app.models.models import SpeakingAttemptAnswer, SpeakingTopic

    topics = db.query(SpeakingTopic).all()
    if not topics:
        return 0

    rows = (db.query(SpeakingAttemptAnswer.topic_id, SpeakingAttemptAnswer.scores)
            .filter(SpeakingAttemptAnswer.topic_id.isnot(None),
                    SpeakingAttemptAnswer.answer_status == 'answered',
                    SpeakingAttemptAnswer.scores.isnot(None),
                    SpeakingAttemptAnswer.duration_ms >= SPEAKING_MIN_MS)
            .all())

    bands = {}
    for topic_id, scores in rows:
        if not isinstance(scores, dict):
            continue
        vals = []
        for v in scores.values():
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        if vals:
            bands.setdefault(topic_id, []).append(sum(vals) / len(vals))

    for t in topics:
        got = bands.get(t.topic_id) or []
        t.difficulty_valid_count = len(got)
        t.difficulty_score = round(sum(got) / len(got), 2) if got else None
        t.difficulty_updated_at = now

    classified = [t for t in topics
                  if (t.difficulty_valid_count or 0) >= MIN_VALID and t.difficulty_score is not None]
    _assign_labels(topics, classified)
    return len(classified)


def _recompute_writing(db: Session, now):
    tasks = db.query(WritingTask).all()
    if not tasks:
        return 0
    # Word count via SQL space-count approximation (avoids loading LONGTEXT essays).
    wc_expr = func.char_length(WritingAnswer.answer_text) \
        - func.char_length(func.replace(WritingAnswer.answer_text, ' ', '')) + 1
    for t in tasks:
        min_words = WRITING_MIN_WORDS.get(t.part_number, 150)
        rows = (
            db.query(WritingAnswer.score, wc_expr)
            .filter(WritingAnswer.task_id == t.task_id, WritingAnswer.score.isnot(None))
            .all()
        )
        bands = [float(score) for score, wc in rows
                 if score is not None and float(score) >= WRITING_MIN_BAND and (wc or 0) >= min_words]
        t.difficulty_valid_count = len(bands)
        t.difficulty_score = round(sum(bands) / len(bands), 2) if bands else None
        t.difficulty_updated_at = now

    classified = [t for t in tasks
                  if (t.difficulty_valid_count or 0) >= MIN_VALID and t.difficulty_score is not None]
    _assign_labels(tasks, classified)
    return len(classified)


def recompute_difficulty(db: Session) -> dict:
    now = get_vietnam_time().replace(tzinfo=None)
    reading_classified = _recompute_sections(db, now, 'reading')
    listening_classified = _recompute_sections(db, now, 'listening')
    writing_classified = _recompute_writing(db, now)
    speaking_classified = _recompute_speaking(db, now)
    db.commit()
    return {
        "reading_classified": reading_classified,
        "listening_classified": listening_classified,
        "writing_classified": writing_classified,
        "speaking_classified": speaking_classified,
        "reading_active": reading_classified >= MIN_CLASSIFIED,
        "listening_active": listening_classified >= MIN_CLASSIFIED,
        "writing_active": writing_classified >= MIN_CLASSIFIED,
        "speaking_active": speaking_classified >= MIN_CLASSIFIED,
    }


def main():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        result = recompute_difficulty(db)
        print("Difficulty recompute:", result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
