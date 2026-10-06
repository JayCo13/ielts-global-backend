"""Answer snapshots — preserve a self-contained copy of a result's reviewable
answers (and optional highlights/notes) so the result review survives future admin
edits that hard-delete the live StudentAnswer/ListeningAnswer + Question rows.

Design notes:
- Answer rows are built 100% server-side at submit time from data already loaded
  during grading, so submitting needs no new client payload.
- Highlights/notes arrive separately via POST /exam-result/{id}/annotations right
  after submit (keeps the two differently-shaped submit bodies untouched).
- Everything is gzipped JSON in a side table to keep storage tiny (~1KB/result).
- Writes are best-effort: a snapshot failure must NEVER break a submit.
"""
import gzip
import json
import logging

from app.models.models import ResultAnswerSnapshot
from app.utils.datetime_utils import get_vietnam_time

logger = logging.getLogger(__name__)


def _gz(obj):
    return gzip.compress(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _ungz(blob):
    if not blob:
        return None
    try:
        return json.loads(gzip.decompress(blob).decode("utf-8"))
    except Exception:
        logger.warning("answer_snapshot: decompress failed", exc_info=True)
        return None


def _get_or_create(db, result_id):
    row = (
        db.query(ResultAnswerSnapshot)
        .filter(ResultAnswerSnapshot.result_id == result_id)
        .first()
    )
    if not row:
        row = ResultAnswerSnapshot(
            result_id=result_id,
            created_at=get_vietnam_time().replace(tzinfo=None),
        )
        db.add(row)
    return row


def build_answer_row(question, student_answer, score):
    """Compact reviewable row. Short keys keep the gzip payload small."""
    return {
        "n": question.question_number,
        "qt": question.question_text,
        "ty": question.question_type,
        "sa": student_answer if student_answer is not None else "",
        "ca": question.correct_answer,
        "sc": float(score) if score is not None else 0,
        "mk": int(question.marks) if question.marks is not None else 1,
        "ex": question.explanation,
        "lo": question.locate,
    }


def save_answers_snapshot(db, result_id, answer_rows):
    """Best-effort: persist the answer rows for a result. Never raises — a failure
    here must not roll back the student's submission. Runs inside the caller's
    transaction so it commits atomically with the answers."""
    try:
        row = _get_or_create(db, result_id)
        row.answers_gz = _gz(answer_rows)
    except Exception:
        logger.warning(
            "answer_snapshot: save_answers failed for result %s", result_id, exc_info=True
        )


def save_annotations(db, result_id, highlights=None, notes=None):
    """Persist the student's highlights/notes (raw client objects). Caller owns commit.
    Both are stored as-is (lists of the exact objects the exam room already keeps in
    localStorage) so review can seed them straight back into the restore mechanism."""
    row = _get_or_create(db, result_id)
    row.annotations_gz = _gz(
        {
            "highlights": highlights or [],
            "notes": notes or [],
        }
    )


def to_detailed_answers(answer_rows):
    """Map compact snapshot rows to the shape /exam-result returns for the UI."""
    out = []
    for i, r in enumerate(answer_rows or [], 1):
        sa = r.get("sa") or ""
        sc = r.get("sc") or 0
        evaluation = "correct" if sc > 0 else ("blank" if sa == "" else "wrong")
        out.append(
            {
                "question_id": None,
                # Listening questions carry no question_number, so "n" is None for
                # every listening row. Fall back to the row's position, which the
                # live path also uses (it numbers 1..40 in question order) — without
                # this the review renders answers with no question numbers.
                "question_number": r.get("n") or i,
                "question_type": r.get("ty"),
                "question_text": r.get("qt"),
                "student_answer": sa,
                "correct_answer": r.get("ca"),
                "explanation": r.get("ex"),
                "locate": r.get("lo"),
                "score": sc,
                "max_marks": r.get("mk"),
                "evaluation": evaluation,
            }
        )
    return out


def load_snapshot(db, result_id):
    row = (
        db.query(ResultAnswerSnapshot)
        .filter(ResultAnswerSnapshot.result_id == result_id)
        .first()
    )
    if not row:
        return None
    return {
        "answers": _ungz(row.answers_gz),
        "annotations": _ungz(row.annotations_gz),
    }
