"""Chấm cả một bài Speaking rồi ghi kết quả xuống DB (docs/speaking-spec.md §5).

Chạy nền sau khi học viên bấm Nộp bài: ba lần gọi AI cho ba part, mỗi lần vài chục giây,
không thể bắt học viên ngồi đợi trong request. Trạng thái nằm trên chính dòng bài thi nên
màn kết quả hỏi được "đang chấm hay xong rồi".

Chỉ bài `completed` mà học viên tự nộp mới được vào đây (§4.4) — đó là kiểm tra đầu tiên
hàm này làm, chứ không tin vào chỗ gọi.

Chạy tay:  python -m app.jobs.speaking_grade <attempt_id>

Global port: runs from FastAPI BackgroundTasks exactly as in VN. On Koyeb a redeploy or
instance restart kills an in-flight grading run; the attempt then sits in 'running' and the
student can re-trigger it after STUCK_AFTER (or cancel + regrade after the cancel window).
Messages that can reach the student (grade_error, part notes) are English.
"""
import logging
import sys
from datetime import timedelta

from app.database import SessionLocal
from app.models.models import SpeakingAttempt, SpeakingAttemptAnswer, User
from app.utils import speaking_grade as G
from app.utils.datetime_utils import get_vietnam_time

logger = logging.getLogger(__name__)

# Một dòng kẹt 'running' lâu hơn thế này coi như tiến trình đã chết giữa chừng và được
# nhặt lại — cùng cách đã dùng cho job sinh gợi ý.
STUCK_AFTER = timedelta(minutes=20)


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def _answers_by_part(db, attempt_id):
    rows = db.query(SpeakingAttemptAnswer).filter(
        SpeakingAttemptAnswer.attempt_id == attempt_id).order_by(
        SpeakingAttemptAnswer.order_index).all()
    grouped = []
    for key, label, parts in G.PART_GROUPS:
        group = [r for r in rows if r.part in parts]
        if group:
            grouped.append((key, label, group))
    return rows, grouped


# Mọi thuộc tính mà bước gọi AI đọc từ một dòng câu trả lời. Chạm hết chúng trước khi
# tách khỏi session, nếu không SQLAlchemy sẽ nạp muộn giữa chừng và lại phải mở kết nối.
_NEEDED = ('final_text', 'final_audio', 'order_index', 'part',
           'topic_title', 'question_text', 'answer_status', 'duration_ms')


def run(attempt_id: int) -> dict:
    """Chấm một bài. Mở session riêng vì hàm này chạy ngoài vòng đời request.

    Ba giai đoạn, cố ý tách rời: đọc → gọi AI (KHÔNG giữ kết nối DB) → ghi bằng session
    mới. Bản đầu giữ nguyên một session suốt cả hàm, mà ba lần gọi AI mất hàng phút; kết
    nối nằm không lâu như vậy bị MySQL đóng, nên đúng lúc ghi kết quả thì vỡ
    `(2013, 'Lost connection to MySQL server during query')` — chấm xong rồi mà mất trắng.
    Đã xảy ra thật trên prod (bài 47). `pool_pre_ping` không cứu được: nó chỉ kiểm tra lúc
    lấy kết nối ra khỏi pool, không kiểm cái đang cầm trên tay.
    """
    db = SessionLocal()
    try:
        attempt = db.query(SpeakingAttempt).filter(
            SpeakingAttempt.attempt_id == attempt_id).first()
        if not attempt:
            raise ValueError(f"Attempt {attempt_id} not found")
        if attempt.status != 'completed' or not attempt.submitted:
            # §4.4: bỏ dở, bị dừng, hay lỗi hệ thống thì lưu lịch sử nhưng không chấm.
            return {'skipped': 'not a completed submission'}
        if attempt.grade_status == 'done':
            return {'skipped': 'already graded'}
        if attempt.grade_status == 'running' and attempt.graded_at \
                and _now() - attempt.graded_at < STUCK_AFTER:
            return {'skipped': 'grading in progress'}

        attempt.grade_status = 'running'
        attempt.grade_error = None
        attempt.graded_at = _now()
        db.commit()

        has_audio = attempt.input_method != 'subtitle'
        test_type = attempt.test_type
        # Feedback 09/09: "nhận xét chung và phân tích ... khi nào có VIP nó mới khởi tạo
        # riêng". Tài khoản thường chỉ xin ĐIỂM — rẻ hơn hẳn và cũng đúng thứ họ được xem.
        # Nạp muộn: speaking_test nạp chính module này (cũng nạp muộn), nhập ở đầu file là
        # vòng lặp import.
        from app.routes.student.speaking_test import has_full_access
        owner = db.query(User).filter(User.user_id == attempt.user_id).first()
        # Chỉ VIP mới được sinh nhận xét (feedback 21/09 bỏ ngoại lệ "bài đầu tiên cũng
        # được xem đầy đủ"). Quyết định ngay tại đây chứ không cắt lúc trả về: sinh rồi
        # giấu đi là trả tiền cho chữ không ai đọc.
        with_feedback = bool(owner) and has_full_access(db, owner, attempt)
        rows, grouped = _answers_by_part(db, attempt_id)
        for row in rows:
            for name in _NEEDED:
                getattr(row, name)
        db.expunge_all()        # các dòng thành bản rời, đọc được mà không cần kết nối
    finally:
        # `finally` chứ không phải `else`: mấy nhánh 'skipped' ở trên đều return thẳng,
        # mà return trong try thì bỏ qua `else` — kết nối sẽ rò mỗi lần bỏ qua một bài.
        db.close()              # trả kết nối về pool TRƯỚC khi gọi AI

    try:
        part_results = []
        per_question = {}

        for key, label, group in grouped:
            # Cả part không có chữ lẫn tiếng thì không gọi AI làm gì.
            if not any((a.final_text or '').strip() or a.final_audio for a in group):
                part_results.append({'part': key, 'label': label, 'criteria': {},
                                     'raw': None, 'band': None,
                                     'note': 'No answers were given in this part.'})
                continue
            out = G.grade_part(label, group, has_audio, with_feedback)
            raw, band = G.part_band({k: v['band'] for k, v in out['criteria'].items()},
                                    has_audio)
            part_results.append({'part': key, 'label': label,
                                 'criteria': out['criteria'], 'raw': raw, 'band': band})
            for q in out['questions']:
                try:
                    per_question[int(q.get('order_index'))] = q
                except (TypeError, ValueError):
                    continue

        graded_parts = [p for p in part_results if p.get('criteria')]
        # §7 — bài lẻ một part KHÔNG có Overall, chỉ có Part Band.
        if test_type == 'full' and graded_parts:
            criteria, overall = G.overall_band(graded_parts, has_audio)
        else:
            criteria, overall = {}, None
    except Exception as e:
        _mark_failed(attempt_id, e)
        logger.exception("Speaking grading for attempt %s failed", attempt_id)
        raise

    # Ghi bằng session mới: kết nối cũ đã nằm không suốt mấy phút gọi AI.
    db = SessionLocal()
    try:
        attempt = db.query(SpeakingAttempt).filter(
            SpeakingAttempt.attempt_id == attempt_id).first()
        if not attempt:
            raise ValueError(f"Attempt {attempt_id} not found")
        for row in db.query(SpeakingAttemptAnswer).filter(
                SpeakingAttemptAnswer.attempt_id == attempt_id).all():
            q = per_question.get(row.order_index)
            if not q:
                continue
            row.scores = q.get('scores') or None
            # 'criteria' = nhận xét TỪNG TIÊU CHÍ cho riêng câu này (feedback 09/09:
            # "bấm vào từng tiêu chí nó sẽ hiện tiếp ra lỗi chi tiết của từng câu").
            row.feedback = {k: q[k] for k in
                            ('relevance', 'comment', 'criteria', 'grammar_errors',
                             'vocabulary_errors', 'audio_quality') if q.get(k)} or None

        attempt.part_results = part_results
        attempt.criteria = criteria or None
        # Bản chấm không kèm nhận xét cần được đánh dấu: nếu học viên lên VIP sau đó, màn
        # kết quả phải mời họ chấm lại thay vì hiện một tab Nhận xét rỗng.
        attempt.grade_error = None if with_feedback else 'scores_only'
        attempt.overall_band = overall
        attempt.grade_status = 'done'
        attempt.graded_at = _now()
        db.commit()
        return {'attempt_id': attempt_id, 'overall': overall,
                'parts': [(p['part'], p['band']) for p in part_results]}
    except Exception as e:
        db.rollback()
        db.close()
        _mark_failed(attempt_id, e)
        logger.exception("Speaking grading for attempt %s failed", attempt_id)
        raise
    finally:
        db.close()


def _mark_failed(attempt_id: int, exc: Exception) -> None:
    """Ghi trạng thái hỏng bằng session RIÊNG.

    Dùng lại session đã vỡ vì mất kết nối thì chính lời ghi này cũng vỡ nốt, và bài lại
    nằm mãi ở 'running' — đúng cái bẫy đang phải sửa.
    """
    db = SessionLocal()
    try:
        attempt = db.query(SpeakingAttempt).filter(
            SpeakingAttempt.attempt_id == attempt_id).first()
        if attempt and attempt.grade_status != 'done':
            attempt.grade_status = 'failed'
            attempt.grade_error = str(exc)[:255]
            db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not record failed status for attempt %s", attempt_id)
    finally:
        db.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("usage: python -m app.jobs.speaking_grade <attempt_id>")
        raise SystemExit(1)
    print(run(int(sys.argv[1])))
