"""Dọn ghi âm Speaking cũ, giữ nguyên lịch sử (docs/speaking-spec.md §9).

Ghi âm là thứ duy nhất trong Speaking lớn dần mà không có trần: spec ước tính 2–3MB mỗi
bài thi đầy đủ. Đĩa đầy thì không chỉ Speaking hỏng — MySQL ngừng ghi được là cả website
chết, nên đây là việc phải có TRƯỚC khi mở Speaking cho học viên thật.

Cái bị xoá chỉ là file âm thanh. Transcript, 4 điểm tiêu chí, nhận xét, thời gian đều ở
lại vĩnh viễn, đúng yêu cầu "lịch sử không bao giờ xoá" của §6.6. Dòng bị dọn được đánh
dấu `audio_expired` để giao diện nói được "bản ghi đã hết hạn lưu trữ" thay vì trông như
học viên chưa từng trả lời.

Ba lớp bảo vệ, một bản ghi chỉ bị xoá khi KHÔNG thuộc lớp nào:
  1. Còn mới hơn KEEP_DAYS ngày.
  2. Nằm trong KEEP_RECENT lần trả lời gần nhất của chính câu hỏi đó.
  3. Là lần điểm cao nhất của câu đó — ứng viên "⭐ câu trả lời tốt nhất của bạn" (§6.3).
     Khi §6.3 cho phép học viên tự chọn câu mẫu thì thêm cờ đó vào đây.

Chạy thử (mặc định, không xoá gì):
    python -m app.jobs.prune_speaking_audio
Xoá thật:
    python -m app.jobs.prune_speaking_audio --commit

Global port: recordings live in Cloudflare R2 (app/utils/speaking_storage.py), so this
deletes R2 objects instead of local files, and the VN disk-usage warning is dropped (R2 has
no disk to fill; Koyeb's local disk is ephemeral). There is no cron on Koyeb — trigger it
from the admin endpoint POST /admin/speaking/jobs/prune-audio?commit=true.
"""
import argparse
import logging
import sys
from datetime import timedelta

from app.database import SessionLocal
from app.models.models import SpeakingAttempt, SpeakingAttemptAnswer
from app.utils import speaking_grade as G
from app.utils import speaking_storage
from app.utils.datetime_utils import get_vietnam_time

logger = logging.getLogger(__name__)

KEEP_RECENT = 3        # số lần gần nhất mỗi câu vẫn nghe lại được
KEEP_DAYS = 30         # dưới mốc này thì không đụng tới, dù có nhiều lần hơn


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def _band(scores) -> float:
    """Điểm của một lần trả lời, để biết lần nào là lần tốt nhất. Không chấm được thì coi
    như thấp nhất — bảo vệ ưu tiên dành cho lần thực sự có điểm."""
    if not isinstance(scores, dict):
        return -1.0
    vals = []
    for v in scores.values():
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    return G.band_for_average(sum(vals) / len(vals)) if vals else -1.0


def _delete(key: str, stats: dict, commit: bool) -> bool:
    """Delete one recording object from R2. Returns False only when an R2 delete was
    attempted and failed — then the DB pointer is kept so the object is not orphaned and
    the next run retries it. Keys that are not R2 recording keys just get cleared."""
    if not key:
        return True
    stats['objects'] += 1
    if not key.startswith(speaking_storage.KEY_PREFIX):
        stats['missing'] += 1
        return True
    if commit and not speaking_storage.delete(key):
        stats['delete_failed'] += 1
        logger.warning("Could not delete R2 recording %s", key)
        return False
    return True


def run(commit: bool = False, keep_recent: int = KEEP_RECENT,
        keep_days: int = KEEP_DAYS, verbose: bool = True) -> dict:
    db = SessionLocal()
    stats = {'scanned': 0, 'pruned': 0, 'objects': 0, 'missing': 0, 'protected': 0,
             'delete_failed': 0, 'committed': bool(commit),
             'r2_configured': speaking_storage.is_configured()}
    try:
        cutoff = _now() - timedelta(days=keep_days)
        rows = (db.query(SpeakingAttemptAnswer, SpeakingAttempt.user_id)
                .join(SpeakingAttempt,
                      SpeakingAttempt.attempt_id == SpeakingAttemptAnswer.attempt_id)
                .filter(SpeakingAttemptAnswer.audio_expired.is_(False))
                .all())

        buckets = {}
        for answer, user_id in rows:
            if not (answer.first_audio or answer.retry_audio):
                continue
            stats['scanned'] += 1
            # Câu AI ứng biến không có id trong ngân hàng; gom chung một rổ mỗi học viên
            # để luật "N lần gần nhất" vẫn áp được thay vì mỗi câu tự bảo vệ chính nó.
            buckets.setdefault((user_id, answer.question_id), []).append(answer)

        for _key, group in buckets.items():
            group.sort(key=lambda a: (a.created_at or _now()), reverse=True)
            best = max(group, key=lambda a: _band(a.scores))
            keep_ids = {a.answer_id for a in group[:keep_recent]}
            if _band(best.scores) >= 0:
                keep_ids.add(best.answer_id)

            for answer in group:
                if answer.answer_id in keep_ids or (answer.created_at or _now()) > cutoff:
                    stats['protected'] += 1
                    continue
                ok_first = _delete(answer.first_audio, stats, commit)
                ok_retry = _delete(answer.retry_audio, stats, commit)
                if commit and not (ok_first and ok_retry):
                    continue
                if commit:
                    answer.first_audio = None
                    answer.retry_audio = None
                    answer.audio_expired = True
                stats['pruned'] += 1

        if commit:
            db.commit()

        if verbose:
            mode = "DELETED" if commit else "dry run, nothing deleted"
            print("%s: %d answers pruned (%d objects), %d kept, %d not in R2, %d delete failures"
                  % (mode, stats['pruned'], stats['objects'], stats['protected'],
                     stats['missing'], stats['delete_failed']), flush=True)
        return stats
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="really delete; default is a dry run")
    ap.add_argument("--keep-recent", type=int, default=KEEP_RECENT)
    ap.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    args = ap.parse_args()
    run(commit=args.commit, keep_recent=args.keep_recent, keep_days=args.keep_days)
    return 0


if __name__ == '__main__':
    sys.exit(main())
