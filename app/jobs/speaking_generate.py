"""Background worker: fill in the generated content for Speaking questions.

docs/speaking-spec.md §2.3. Saving a topic only stores what the admin pasted; the
outline, the four band-level model answers and the vocabulary are produced here. One
Part 1 topic is ~10 questions and each needs two Gemini calls, so this cannot run
inside the save request.

State lives on the question itself (`gen_status`), which is what makes a single
question retryable without touching the rest of the topic:

    pending -> running -> done
                       -> failed  (gen_error holds why)

Anything left in `running` by a crash is picked up again after RUNNING_STALE_MINUTES;
without that a container restart mid-generation would strand those rows forever.

Usage:
    python -m app.jobs.speaking_generate                  # every pending question
    python -m app.jobs.speaking_generate --topic 12       # one topic
    python -m app.jobs.speaking_generate --question 345   # one question, force
    python -m app.jobs.speaking_generate --retry-failed
"""
import argparse
import logging
import sys
from datetime import timedelta

from sqlalchemy import or_

from app.database import SessionLocal
from app.models.models import (
    SpeakingQuestion, SpeakingSuggestion, SpeakingTopic, SpeakingVocabulary,
)
from app.utils.datetime_utils import get_vietnam_time
from app.utils.speaking_gen import generate_suggestion, generate_vocabulary, summarise

logger = logging.getLogger(__name__)

RUNNING_STALE_MINUTES = 15


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def claim(db, question) -> bool:
    """Mark a question as running. False if someone else got there first."""
    updated = (db.query(SpeakingQuestion)
               .filter(SpeakingQuestion.question_id == question.question_id,
                       SpeakingQuestion.gen_status != 'running')
               .update({'gen_status': 'running', 'gen_error': None},
                       synchronize_session=False))
    db.commit()
    return bool(updated)


def generate_one(db, question, topic_title: str = '') -> str:
    """Generate both blocks for one question and store them. Returns a summary line.

    Both blocks are written in a single transaction: a question that ends up `done`
    with vocabulary but no model answers would look complete in the admin table and
    fail silently for the student.
    """
    if not topic_title:
        topic = db.query(SpeakingTopic).filter(
            SpeakingTopic.topic_id == question.topic_id).first()
        topic_title = topic.title if topic else ''

    suggestion = generate_suggestion(question.part, question.content, topic_title)
    vocab_rows = generate_vocabulary(question.part, question.content, topic_title)

    db.query(SpeakingSuggestion).filter(
        SpeakingSuggestion.question_id == question.question_id).delete()
    db.query(SpeakingVocabulary).filter(
        SpeakingVocabulary.question_id == question.question_id).delete()

    db.add(SpeakingSuggestion(
        question_id=question.question_id,
        outline=suggestion['outline'],
        samples=suggestion['samples'],
        model=suggestion['model'],
        created_at=_now(),
    ))
    for band, order, term, meaning, example in vocab_rows:
        db.add(SpeakingVocabulary(
            question_id=question.question_id, band_level=band, order_index=order,
            term=term, meaning_vi=meaning, example=example,
        ))

    question.gen_status = 'done'
    question.gen_error = None
    db.add(question)
    db.commit()
    return summarise(suggestion, vocab_rows)


def pending_query(db, topic_id=None, question_id=None, retry_failed=False):
    q = db.query(SpeakingQuestion)
    if question_id:
        return q.filter(SpeakingQuestion.question_id == question_id)
    stale_before = _now() - timedelta(minutes=RUNNING_STALE_MINUTES)
    states = [SpeakingQuestion.gen_status == 'pending']
    if retry_failed:
        states.append(SpeakingQuestion.gen_status == 'failed')
    # A row stuck in 'running' past the stale window was orphaned by a crash.
    states.append((SpeakingQuestion.gen_status == 'running') &
                  (SpeakingQuestion.created_at < stale_before))
    q = q.filter(or_(*states))
    if topic_id:
        q = q.filter(SpeakingQuestion.topic_id == topic_id)
    return q.order_by(SpeakingQuestion.topic_id, SpeakingQuestion.part,
                      SpeakingQuestion.order_index)


def run(topic_id=None, question_id=None, retry_failed=False, limit=0, verbose=True):
    db = SessionLocal()
    stats = {'done': 0, 'failed': 0, 'skipped': 0}
    try:
        rows = pending_query(db, topic_id, question_id, retry_failed).all()
        if limit:
            rows = rows[:limit]
        if verbose:
            print("can sinh: %d cau hoi" % len(rows), flush=True)

        titles = {}
        for question in rows:
            if question.topic_id not in titles:
                t = db.query(SpeakingTopic).filter(
                    SpeakingTopic.topic_id == question.topic_id).first()
                titles[question.topic_id] = t.title if t else ''

            if not question_id and not claim(db, question):
                stats['skipped'] += 1
                continue

            try:
                info = generate_one(db, question, titles[question.topic_id])
                stats['done'] += 1
                if verbose:
                    print("  [done]   q%-6s %-14s %s" % (question.question_id, question.part, info), flush=True)
            except Exception as exc:                       # noqa: BLE001 — one bad question must not stop the batch
                db.rollback()
                msg = f"{type(exc).__name__}: {exc}"[:255]
                (db.query(SpeakingQuestion)
                   .filter(SpeakingQuestion.question_id == question.question_id)
                   .update({'gen_status': 'failed', 'gen_error': msg},
                           synchronize_session=False))
                db.commit()
                stats['failed'] += 1
                logger.warning("speaking_generate failed for q%s: %s", question.question_id, msg)
                if verbose:
                    print("  [FAILED] q%-6s %s" % (question.question_id, msg), flush=True)

        if verbose:
            print("xong: %d thanh cong, %d loi, %d bo qua"
                  % (stats['done'], stats['failed'], stats['skipped']), flush=True)
        return stats
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", type=int, default=None, help="chi sinh cho 1 topic")
    ap.add_argument("--question", type=int, default=None, help="chi sinh cho 1 cau hoi (bo qua trang thai)")
    ap.add_argument("--retry-failed", action="store_true", help="lam lai ca nhung cau da that bai")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    run(args.topic, args.question, args.retry_failed, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
