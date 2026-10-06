"""Trang phân tích chi tiết — Speaking Learning Hub (docs/speaking-spec.md §6).

Đây là chỗ học viên HỌC, khác màn kết quả (§5.6) chỉ để xem điểm. Vì thế mọi thứ ở đây
xoay quanh MỘT CÂU HỎI qua nhiều lần trả lời, chứ không xoay quanh một bài thi: cùng một
câu có thể được hỏi lại ở bài sau, và §6.6 bắt giữ toàn bộ lịch sử để thấy tiến bộ
`5.5 → 6.0 → 6.5`.

§6.8 là ràng buộc cứng của cả module: **không gọi AI chỉ vì học viên mở trang.** Tất cả
endpoint ở đây chỉ đọc dữ liệu đã có sẵn — chấm điểm đã chạy lúc nộp bài, gợi ý và từ vựng
đã sinh lúc admin thêm đề. Chức năng nào cần AI thì phải nằm sau một nút học viên tự bấm.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (SpeakingAttempt, SpeakingAttemptAnswer, SpeakingQuestion,
                               SpeakingQuestionProgress, SpeakingSuggestion,
                               SpeakingVocabulary, User)
from app.routes.admin.auth import get_current_student
from app.utils import speaking_grade as G
from app.utils.datetime_utils import get_vietnam_time
from app.utils.speaking_gate import require_speaking_access
from app.routes.student.speaking_test import is_vip, has_full_access, free_question_ids

logger = logging.getLogger(__name__)
router = APIRouter()


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def _band(scores) -> Optional[float]:
    """Điểm một lần trả lời = trung bình các tiêu chí, làm tròn qua `G.band_for_average`
    để khớp với Overall và Part Band (xem phần hiệu chỉnh ở app/utils/speaking_grade.py)."""
    if not isinstance(scores, dict):
        return None
    vals = []
    for v in scores.values():
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    return G.band_for_average(sum(vals) / len(vals)) if vals else None


def _answers_for_question(db: Session, user_id: int, question_id: int
                          ) -> List[SpeakingAttemptAnswer]:
    """Mọi lần học viên đã trả lời câu này, mới nhất trước — xuyên qua các bài thi.

    §6.6: lịch sử không bao giờ xoá, kể cả khi ghi âm đã bị dọn (§9).
    """
    return (db.query(SpeakingAttemptAnswer)
            .join(SpeakingAttempt,
                  SpeakingAttempt.attempt_id == SpeakingAttemptAnswer.attempt_id)
            .filter(SpeakingAttempt.user_id == user_id,
                    SpeakingAttemptAnswer.question_id == question_id)
            .order_by(SpeakingAttemptAnswer.created_at.desc(),
                      SpeakingAttemptAnswer.answer_id.desc())
            .all())


def pick_sample(answers: List[SpeakingAttemptAnswer],
                chosen_id: Optional[int]) -> tuple:
    """(câu mẫu, do-học-viên-chọn) theo đúng luật §6.3.

        NẾU học viên đã tự chọn  → dùng câu đó, KHÔNG tự thay dù sau này có band cao hơn
        NGƯỢC LẠI                → band cao nhất; bằng điểm thì lấy lần MỚI NHẤT

    `answers` đã sắp mới-nhất-trước, nên `max` giữ phần tử đầu tiên khi bằng điểm — đúng
    vế "mới nhất" mà không cần so ngày lần nữa.
    """
    answered = [a for a in answers if a.answer_status == 'answered']
    if chosen_id is not None:
        picked = next((a for a in answered if a.answer_id == chosen_id), None)
        if picked is not None:
            return picked, True
        # Câu đã chọn không còn (bài thi bị xoá) — quay về tự chọn thay vì trả con trỏ gãy.
    if not answered:
        return None, False
    scored = [a for a in answered if _band(a.scores) is not None]
    if not scored:
        return answered[0], False
    return max(scored, key=lambda a: _band(a.scores)), False


def _progress(db: Session, user_id: int, question_ids: List[int]) -> dict:
    if not question_ids:
        return {}
    rows = (db.query(SpeakingQuestionProgress)
            .filter(SpeakingQuestionProgress.user_id == user_id,
                    SpeakingQuestionProgress.question_id.in_(question_ids)).all())
    return {r.question_id: r for r in rows}


def _own_attempt(db: Session, attempt_id: int, user: User) -> SpeakingAttempt:
    attempt = db.query(SpeakingAttempt).filter(
        SpeakingAttempt.attempt_id == attempt_id,
        SpeakingAttempt.user_id == user.user_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Test not found.")
    return attempt


# ── §6.1 Trang Analysis ────────────────────────────────────────────────────────────────

PART_LABELS = {'part1': 'Part 1', 'part2': 'Part 2', 'part2_followup': 'Part 2',
               'part3': 'Part 3'}


@router.get("/speaking/analysis/attempts/{attempt_id}", response_model=dict)
async def analysis_overview(attempt_id: int,
                            current_student: User = Depends(get_current_student),
                            db: Session = Depends(get_db)):
    """Danh sách câu của một bài, kèm trạng thái đã xem và nhãn câu mẫu (§6.1).

    Bản ghi trả về theo từng Part để dựng được playlist "nghe lại cả bài" — §6.1 muốn
    tua theo Part chứ không phải một khối liền.
    """
    require_speaking_access(current_student)
    attempt = _own_attempt(db, attempt_id, current_student)
    rows = (db.query(SpeakingAttemptAnswer)
            .filter(SpeakingAttemptAnswer.attempt_id == attempt_id)
            .order_by(SpeakingAttemptAnswer.order_index).all())

    qids = [r.question_id for r in rows if r.question_id]
    progress = _progress(db, current_student.user_id, qids)

    # Câu nào đang là câu mẫu của học viên — để gắn ⭐ ngay ở danh sách.
    starred = set()
    for qid in set(qids):
        chosen = progress.get(qid).sample_answer_id if progress.get(qid) else None
        sample, _manual = pick_sample(
            _answers_for_question(db, current_student.user_id, qid), chosen)
        if sample is not None:
            starred.add(sample.answer_id)

    # Điểm từng Part lấy từ lần chấm, không tính lại: bài thi đủ 3 part thì học viên phải
    # thấy được mình mạnh yếu ở part nào, còn điểm trên đầu trang vẫn là Overall.
    part_band = {}
    for p in (attempt.part_results or []):
        if isinstance(p, dict) and p.get('label'):
            part_band[p['label']] = p.get('band')

    # Nhận xét từng câu là của VIP, trừ BÀI ĐẦU TIÊN của tài khoản (feedback 23/09).
    # Bài chấm lúc tài khoản còn VIP vẫn nằm trong DB, nên phải cắt ở ĐÂY chứ không dựa vào
    # việc "chấm cho tài khoản thường thì không sinh nhận xét" — VIP hết hạn là đọc lại được.
    # ĐIỂM thì vẫn trả: đó là phần tổng quan ai cũng được xem.
    show_comments = has_full_access(db, current_student, attempt)

    parts, items = [], []
    for r in rows:
        label = PART_LABELS.get(r.part, r.part)
        if label not in parts:
            parts.append(label)
        p = progress.get(r.question_id)
        items.append({
            "answer_id": r.answer_id,
            "question_id": r.question_id,
            "order_index": r.order_index,
            "part": r.part,
            "part_label": label,
            "question_text": r.question_text,
            "topic_title": r.topic_title,
            "status": r.answer_status,
            "band": _band(r.scores),
            # Tab "Đề & Bài" và bản xuất PDF/Word cần đọc lại nguyên văn bài nói cùng nhận
            # xét của từng câu; lấy luôn ở đây để ba tab dùng chung một lần gọi.
            "answer_text": r.final_text,
            "comment": ((r.feedback or {}).get('comment')
                        if show_comments and isinstance(r.feedback, dict) else None),
            "scores": r.scores or None,
            "has_audio": bool(r.final_audio),
            "audio_expired": bool(r.audio_expired),
            "duration_ms": r.duration_ms,
            # §6.1: mở là tự đánh dấu, nhưng phải LƯU LẠI — nên trạng thái đến từ DB.
            "viewed": bool(p and p.viewed_at),
            "is_sample": r.answer_id in starred,
            "is_ai_followup": bool(r.is_ai_followup),
        })

    return {
        "attempt_id": attempt.attempt_id,
        "test_type": attempt.test_type,
        "mode": attempt.mode,
        "status": attempt.status,
        "grade_status": attempt.grade_status,
        "overall_band": attempt.overall_band,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "parts": [{"label": label, "band": part_band.get(label)} for label in parts],
        # §6.1 "Thi lại đúng bộ câu hỏi cũ": đề đã đóng băng trong plan nên thi lại được;
        # điểm bài gốc không đổi, bài mới là "Kết quả Retake".
        "can_retake": attempt.status == 'completed',
        "questions": items,
    }


# ── §6.2 + §6.3 + §6.6 Chi tiết một câu hỏi ────────────────────────────────────────────

def _serialise_history(a: SpeakingAttemptAnswer, sample_id: Optional[int]) -> dict:
    return {
        "answer_id": a.answer_id,
        "attempt_id": a.attempt_id,
        "band": _band(a.scores),
        "scores": a.scores or None,
        "status": a.answer_status,
        "has_audio": bool(a.final_audio),
        "audio_expired": bool(a.audio_expired),
        "text": a.final_text,
        "duration_ms": a.duration_ms,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "is_sample": a.answer_id == sample_id,
    }


@router.get("/speaking/analysis/questions/{question_id}", response_model=dict)
async def analysis_question(question_id: int,
                            current_student: User = Depends(get_current_student),
                            db: Session = Depends(get_db)):
    """Toàn bộ những gì §6.2 yêu cầu cho một câu, cộng lịch sử §6.6.

    Không gọi AI (§6.8): gợi ý và từ vựng lấy từ dữ liệu admin đã sinh sẵn, nhận xét và
    lỗi lấy từ lần chấm lúc nộp bài.
    """
    require_speaking_access(current_student)
    # feedback 07/09: phân tích từng câu là của VIP. Chặn ở máy chủ chứ không chỉ ẩn nút,
    # đây là chỗ tốn token nhất và cũng là thứ đáng tiền nhất của gói VIP.
    # feedback 23/09: mở lại cho những câu thuộc BÀI ĐẦU TIÊN của tài khoản — người mới
    # phải xem được một lần thì mới biết gói VIP có gì.
    vip = is_vip(current_student, db)
    if not vip and question_id not in free_question_ids(db, current_student):
        raise HTTPException(status_code=403,
                            detail="Detailed per-question analysis is a VIP feature.")
    question = db.query(SpeakingQuestion).filter(
        SpeakingQuestion.question_id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found.")

    answers = _answers_for_question(db, current_student.user_id, question_id)
    if not answers:
        raise HTTPException(status_code=404, detail="You have not answered this question yet.")

    prog = _progress(db, current_student.user_id, [question_id]).get(question_id)
    sample, manual = pick_sample(answers, prog.sample_answer_id if prog else None)
    current = next((a for a in answers if a.answer_status == 'answered'), answers[0])

    fb = current.feedback if isinstance(current.feedback, dict) else {}
    suggestion = db.query(SpeakingSuggestion).filter(
        SpeakingSuggestion.question_id == question_id).first()
    vocab = (db.query(SpeakingVocabulary)
             .filter(SpeakingVocabulary.question_id == question_id)
             .order_by(SpeakingVocabulary.band_level,
                       SpeakingVocabulary.order_index).all())

    # §6.4 "Trả lời lại câu hỏi" không có đường riêng: nó là luyện một câu của §7
    # (`POST /speaking/forecast/start` kèm question_id). Giao diện cần đúng hai thứ này
    # để gọi được, nên trả kèm luôn thay vì bắt hỏi thêm một vòng.
    section = 'part3' if question.part == 'part3' else (
        'part1' if question.part == 'part1' else 'part2')

    return {
        "question_id": question_id,
        "question_text": question.content,
        "part": question.part,
        "topic_id": question.topic_id,
        "retry_section": section,
        "times_answered": sum(1 for a in answers if a.answer_status == 'answered'),
        "viewed": bool(prog and prog.viewed_at),

        # Câu trả lời hiện tại (§6.2)
        "current": {
            "answer_id": current.answer_id,
            "attempt_id": current.attempt_id,
            "text": current.final_text,
            "has_audio": bool(current.final_audio),
            "audio_expired": bool(current.audio_expired),
            "duration_ms": current.duration_ms,
            "status": current.answer_status,
            "band": _band(current.scores),
            "scores": current.scores or None,
            "relevance": fb.get('relevance'),
            "comment": fb.get('comment'),
            # feedback 09/09: "đẩy sâu nhận xét cho từng câu — bấm vào từng tiêu chí nó
            # sẽ hiện tiếp ra lỗi chi tiết". Mỗi tiêu chí có một câu kết luận riêng cho
            # RIÊNG câu này cùng danh sách lỗi trích dẫn nguyên văn.
            # Dạng {pronunciation: {verdict, issues: [{quote, problem, fix}]}, ...}.
            "criteria": fb.get('criteria') or {},
            # Dạng {incorrect, correct, type, impact} / {incorrect, better, why} — giao
            # diện tô đỏ phần sai, xanh phần sửa, bấm vào hiện giải thích.
            "grammar_errors": fb.get('grammar_errors') or [],
            "vocabulary_errors": fb.get('vocabulary_errors') or [],
            # §6.2: chỉ để khuyên, KHÔNG tính vào 4 tiêu chí IELTS.
            "audio_quality": fb.get('audio_quality'),
        },

        # Khung "Tự soạn bài mẫu" và hai nút AI trong đó là của VIP (feedback 23/09) —
        # KHÁC với phần phân tích ở trên, vốn còn mở cho bài đầu tiên. Máy chủ quyết định,
        # giao diện chỉ đọc cờ này để khoá đúng chỗ.
        "is_vip": vip,
        "compose_locked": not vip,

        # §6.3. Bài mẫu dạng CHỮ được ưu tiên vì học viên đã chủ động chọn nó, nhưng nhãn
        # nói rõ đây là bản soạn — không để tưởng đây là bài mình từng nói được. Chữ có thể
        # do AI viết hoặc do chính học viên gõ trong khung soạn bài (feedback 23/09); DB
        # không ghi nguồn nên nhãn dùng chung một cách nói đúng cho cả hai.
        "sample": ({"answer_id": None, "text": prog.sample_text, "band": None,
                    "has_audio": False, "audio_expired": False, "is_sample": True,
                    "chosen_by_user": True, "from_ai": True,
                    "label": "Your model answer (prepared text)"}
                   if (prog and prog.sample_text) else
                   ({**_serialise_history(sample, sample.answer_id),
                     "chosen_by_user": manual, "from_ai": False,
                     "label": "Your chosen model answer" if manual
                              else "Your best answer"}
                    if sample is not None else None)),

        # §6.6 — mọi lần, kể cả lần ghi âm đã hết hạn lưu trữ
        "history": [_serialise_history(a, sample.answer_id if sample else None)
                    for a in answers],

        # §6.4: hai mục này là DỮ LIỆU SẴN, không gọi AI
        "outline": suggestion.outline if suggestion else None,
        "band_samples": suggestion.samples if suggestion else None,
        "vocabulary": [{"term": v.term, "meaning": v.meaning_vi, "example": v.example,
                        "band_level": v.band_level} for v in vocab],
    }


@router.post("/speaking/analysis/questions/{question_id}/viewed", response_model=dict)
async def mark_viewed(question_id: int,
                      current_student: User = Depends(get_current_student),
                      db: Session = Depends(get_db)):
    """§6.1: mở phần phân tích là tự đánh dấu đã xem — nhưng phải lưu lại."""
    require_speaking_access(current_student)
    row = _upsert_progress(db, current_student.user_id, question_id)
    if row.viewed_at is None:
        row.viewed_at = _now()
        db.commit()
    return {"question_id": question_id, "viewed": True}


class ChooseSample(BaseModel):
    answer_id: Optional[int] = None      # None = bỏ chọn, trả về cách tự chọn
    # Câu do AI viết. Đặt cái này thì `answer_id` bị xoá và ngược lại — một lúc chỉ có một
    # câu mẫu, và học viên phải biết nó đến từ đâu.
    text: Optional[str] = None


@router.post("/speaking/analysis/questions/{question_id}/sample", response_model=dict)
async def choose_sample(question_id: int, payload: ChooseSample,
                        current_student: User = Depends(get_current_student),
                        db: Session = Depends(get_db)):
    """§6.3: học viên tự chọn câu mẫu, và lựa chọn đó không bị hệ thống ghi đè."""
    require_speaking_access(current_student)
    answers = _answers_for_question(db, current_student.user_id, question_id)
    if payload.answer_id is not None:
        target = next((a for a in answers if a.answer_id == payload.answer_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Answer not found.")
        if target.answer_status != 'answered':
            raise HTTPException(status_code=400,
                                detail="This attempt has no answer to use as a model.")

    text = (payload.text or '').strip()
    row = _upsert_progress(db, current_student.user_id, question_id)
    if text:
        row.sample_text = text[:8000]
        row.sample_answer_id = None
    else:
        row.sample_answer_id = payload.answer_id
        row.sample_text = None
    db.commit()

    if text:
        return {"question_id": question_id, "sample_answer_id": None,
                "sample_text": text, "chosen_by_user": True, "from_ai": True}
    sample, manual = pick_sample(answers, payload.answer_id)
    return {"question_id": question_id,
            "sample_answer_id": sample.answer_id if sample else None,
            "chosen_by_user": manual, "from_ai": False}


def _upsert_progress(db: Session, user_id: int, question_id: int) -> SpeakingQuestionProgress:
    row = db.query(SpeakingQuestionProgress).filter(
        SpeakingQuestionProgress.user_id == user_id,
        SpeakingQuestionProgress.question_id == question_id).first()
    if row is None:
        row = SpeakingQuestionProgress(user_id=user_id, question_id=question_id)
        db.add(row)
        db.flush()
    return row
