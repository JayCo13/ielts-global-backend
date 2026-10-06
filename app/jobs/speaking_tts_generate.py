"""Render every examiner line to audio, once per voice.

docs/speaking-spec.md decision #3. Three kinds of clip, all stored the same way:

  * the fixed script — same words in every test, so a handful of clips per voice;
  * one clip per question in the bank;
  * the two lines that name a topic ("Let's talk about X", the Part 3 bridge).

Work is decided by fingerprint, not by presence: a clip whose stored fingerprint no
longer matches the current wording is regenerated. That is what makes an edited question
safe — otherwise the file is still there, still plays, and still says the old thing.

Usage:
    python -m app.jobs.speaking_tts_generate                 # everything missing or stale
    python -m app.jobs.speaking_tts_generate --topic 12
    python -m app.jobs.speaking_tts_generate --voice Achird
    python -m app.jobs.speaking_tts_generate --dry-run
"""
import argparse
import logging
import sys

from app.database import SessionLocal
from app.models.models import SpeakingQuestion, SpeakingTopic, SpeakingTts
from app.utils.datetime_utils import get_vietnam_time
from app.utils import speaking_scripts as scripts
from app.utils.speaking_tts import (TtsQuota, default_voice, synthesize,
                                    text_fingerprint, voice_names)

logger = logging.getLogger(__name__)


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def wanted_clips(db, topic_id=None):
    """[(cache_key, text)] — every line that should have audio.

    The fixed script is skipped when a single topic is requested; it has nothing to do
    with that topic and would be re-checked on every run for no reason.
    """
    out = []
    if topic_id is None:
        for name, text in scripts.SCRIPTS.items():
            out.append((scripts.key_for_script(name), text))

    topics = db.query(SpeakingTopic)
    if topic_id:
        topics = topics.filter(SpeakingTopic.topic_id == topic_id)
    for topic in topics.all():
        if topic.part == 'part1':
            out.append((scripts.key_for_topic_intro(topic.topic_id),
                        scripts.topic_intro(topic.title)))
        else:
            out.append((scripts.key_for_part3_intro(topic.topic_id),
                        scripts.part3_intro(topic.title)))

    questions = db.query(SpeakingQuestion)
    if topic_id:
        questions = questions.filter(SpeakingQuestion.topic_id == topic_id)
    for q in questions.order_by(SpeakingQuestion.question_id).all():
        out.append((scripts.key_for_question(q.question_id), q.content))

    return out


def run(topic_id=None, voices=None, dry_run=False, limit=0, verbose=True):
    db = SessionLocal()
    voices = tuple(voices or voice_names())
    stats = {'made': 0, 'stale': 0, 'skipped': 0, 'failed': 0, 'bytes': 0,
             'quota_stopped': False, 'remaining': 0}
    try:
        clips = wanted_clips(db, topic_id)
        existing = {
            (row.cache_key, row.voice): row
            for row in db.query(SpeakingTts).filter(
                SpeakingTts.cache_key.in_([k for k, _ in clips])).all()
        } if clips else {}

        todo = []
        for cache_key, text in clips:
            for voice in voices:
                want = text_fingerprint(text, voice)
                row = existing.get((cache_key, voice))
                if row is not None and row.fingerprint == want and row.audio:
                    stats['skipped'] += 1
                    continue
                if row is not None:
                    stats['stale'] += 1
                todo.append((cache_key, voice, text, want, row))

        # Làm XONG một giọng rồi mới sang giọng kế, giọng mặc định đi đầu. Hạn mức ngày
        # của Gemini TTS rất dễ chạm; xếp kiểu cũ (mỗi câu làm đủ mọi giọng rồi mới sang
        # câu sau) thì lúc hết hạn mức những câu cuối không có giọng nào — học viên gặp
        # đúng câu đó là bài thi câm. Xếp theo giọng thì tệ nhất cũng có vài giọng dùng
        # được trọn vẹn, và giọng đang thêm dở sẽ hoàn tất ở lần chạy sau.
        order = {v: i for i, v in enumerate(voices)}
        first = default_voice()
        todo.sort(key=lambda item: (0 if item[1] == first else 1, order.get(item[1], 99)))

        if limit:
            todo = todo[:limit]
        if verbose:
            print("can sinh: %d clip (%d da co, %d qua han)"
                  % (len(todo), stats['skipped'], stats['stale']), flush=True)
        if dry_run:
            for cache_key, voice, text, _fp, _row in todo[:20]:
                print("  [thu] %-24s %-9s %s" % (cache_key, voice, text[:52].replace('\n', ' ')), flush=True)
            return stats

        for i, (cache_key, voice, text, want, row) in enumerate(todo, 1):
            try:
                audio, ms = synthesize(text, voice)
            except TtsQuota as exc:
                # The cap resets tomorrow, so stop here instead of marking every
                # remaining line failed. Nothing is lost: the next run picks up exactly
                # where this one stopped, because work is decided by fingerprint.
                stats['quota_stopped'] = True
                stats['remaining'] = len(todo) - i + 1
                if verbose:
                    print("  [STOP] %s — %d clips left, run again after the quota resets"
                          % (exc, stats['remaining']), flush=True)
                logger.warning("speaking_tts stopped on quota with %d clips left", stats['remaining'])
                break
            except Exception as exc:                    # noqa: BLE001 — one bad line must not stop the batch
                stats['failed'] += 1
                logger.warning("speaking_tts failed for %s/%s: %s", cache_key, voice, exc)
                if verbose:
                    print("  [LOI]  %-24s %-9s %s" % (cache_key, voice, str(exc)[:70]), flush=True)
                continue

            if row is None:
                row = SpeakingTts(cache_key=cache_key, voice=voice)
            row.fingerprint = want
            row.audio = audio
            row.duration_ms = ms
            row.created_at = _now()
            db.add(row)
            db.commit()

            stats['made'] += 1
            stats['bytes'] += len(audio)
            if verbose and (i % 10 == 0 or i == len(todo)):
                print("  ...%d/%d (%.1f MB)" % (i, len(todo), stats['bytes'] / 1048576), flush=True)

        if verbose:
            print("xong: %d clip moi, %d loi, %d bo qua, tong %.1f MB"
                  % (stats['made'], stats['failed'], stats['skipped'],
                     stats['bytes'] / 1048576), flush=True)
        return stats
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", type=int, default=None)
    ap.add_argument("--voice", action="append", default=None, help="lap lai de chon nhieu giong")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.topic, args.voice, args.dry_run, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
