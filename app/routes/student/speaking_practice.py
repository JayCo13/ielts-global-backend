"""Các nút CÓ gọi AI trong trang phân tích (docs/speaking-spec.md §6.4–§6.5) + §6.7.

Tách khỏi `speaking_analysis.py` một cách cố ý: file kia chỉ đọc dữ liệu có sẵn và tuyệt
đối không gọi AI (§6.8). Mọi thứ tốn tiền nằm ở đây, sau một nút học viên tự bấm.

"Trả lời lại câu hỏi" của §6.4 KHÔNG có endpoint riêng: nó chính là luyện một câu của §7
(`POST /student/speaking/forecast/start` kèm `question_id`). Đi đường đó thì câu trả lời
mới tự chảy qua phòng thi, được chấm bằng đúng bộ chấm cũ, và rơi vào lịch sử §6.6 —
viết một đường song song chỉ để lệch nhau về sau.
"""
import hashlib
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (SpeakingAttempt, SpeakingAttemptAnswer, SpeakingQuestion, SpeakingTts, User)
from app.routes.admin.auth import get_current_student
from app.utils import speaking_audio, speaking_improve as I
from app.utils import speaking_quota as Q
from app.utils.speaking_gate import require_speaking_access
from app.utils.speaking_tts import (default_voice, natural_model_audio, synthesize,
                                    text_fingerprint, voice_names)
from app.routes.student.speaking_test import _user_from_token

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_ANSWER_CHARS = 4000
MAX_PRACTICE_CHARS = 300        # "từ / cụm / câu", không phải cả bài
MAX_AUDIO_BYTES = 8 * 1024 * 1024


def _question(db: Session, question_id: int) -> SpeakingQuestion:
    q = db.query(SpeakingQuestion).filter(
        SpeakingQuestion.question_id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found.")
    return q


def _own_answer(db: Session, answer_id: int, user: User) -> SpeakingAttemptAnswer:
    row = (db.query(SpeakingAttemptAnswer)
           .join(SpeakingAttempt,
                 SpeakingAttempt.attempt_id == SpeakingAttemptAnswer.attempt_id)
           .filter(SpeakingAttemptAnswer.answer_id == answer_id,
                   SpeakingAttempt.user_id == user.user_id).first())
    if not row:
        raise HTTPException(status_code=404, detail="Answer not found.")
    return row


async def _read_audio(upload: UploadFile) -> tuple:
    """(bytes, mime) đã sẵn sàng gửi cho AI nghe.

    Luôn nén sang Opus/Ogg trước, KHÔNG gửi thẳng blob của trình duyệt: MediaRecorder trả
    về `audio/webm;codecs=opus`, mà chuỗi mime kèm tham số `codecs` thì Gemini từ chối —
    phòng thi không dính vì nó nén trước khi lưu, còn hai chức năng luyện ở đây thì gửi
    thẳng. Nén hỏng thì mới dùng bản gốc, và cắt phần `;codecs=...` đi.
    """
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The recording is empty.")
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="The recording is too long.")
    ogg, ext = await run_in_threadpool(speaking_audio.to_opus, raw)
    if ext:
        return ogg, 'audio/ogg'
    return raw, (upload.content_type or 'audio/webm').split(';')[0]


def _debug_tag(user: User, kind: str, target: str):
    """TẠM: chỉ tài khoản test mới bị lưu bản ghi để chỉnh ngưỡng cửa kiểm (19/09)."""
    from app.utils.speaking_gate import unlimited_for_testing
    return f"{kind}_{user.user_id}_{target[:24]}" if unlimited_for_testing(user) else None


def _no_score(db: Session, user: User, target: str, problem: str, **extra) -> dict:
    """Trả lời "không chấm được" theo đúng dạng giao diện đang đọc: `score` rỗng kèm
    `problem`. Cả bốn màn luyện đều đã hiện `problem` khi `score` rỗng, nên không phải sửa
    giao diện."""
    return {"target_text": target, "score": None, "problem": problem,
            "quota": Q.quota(db, user), **extra}


def _ai_error(exc: Exception) -> HTTPException:
    logger.warning("Speaking AI failed: %s", exc)
    return HTTPException(status_code=503,
                         detail="The AI is busy right now — please try again in a few minutes.")


# ── §6.4 ✨ Cải thiện câu trả lời ───────────────────────────────────────────────────────

class ImproveIn(BaseModel):
    answer_id: Optional[int] = None    # dùng chính câu đã nói
    text: Optional[str] = None         # hoặc chữ học viên gõ vào
    target_band: str = '6.0-6.5'


@router.post("/speaking/practice/questions/{question_id}/improve", response_model=dict)
async def improve(question_id: int, payload: ImproveIn,
                  current_student: User = Depends(get_current_student),
                  db: Session = Depends(get_db)):
    """Viết lại chính câu học viên đã nói, giữ nguyên ý, nâng lên band mục tiêu."""
    require_speaking_access(current_student)
    question = _question(db, question_id)

    source = (payload.text or '').strip()
    if payload.answer_id is not None:
        source = (_own_answer(db, payload.answer_id, current_student).final_text or '').strip()
    if not source:
        raise HTTPException(status_code=400,
                            detail="There is no answer to improve yet.")

    try:
        data = await run_in_threadpool(I.improve_answer, question.content,
                                       source[:MAX_ANSWER_CHARS], payload.target_band)
    except Exception as e:
        raise _ai_error(e)

    return {"question_id": question_id, "target_band": payload.target_band,
            "original": source, "improved": data.get('improved'),
            "changes": data.get('changes') or [], "note": data.get('note')}


# ── §6.4 💡 Tạo câu trả lời từ ý tưởng ─────────────────────────────────────────────────

class IdeasIn(BaseModel):
    ideas: str
    target_band: str = '6.0-6.5'


@router.post("/speaking/practice/questions/{question_id}/from-ideas", response_model=dict)
async def from_ideas(question_id: int, payload: IdeasIn,
                     current_student: User = Depends(get_current_student),
                     db: Session = Depends(get_db)):
    """Ý tưởng thô (tiếng Việt cũng được) → câu trả lời hoàn chỉnh, bám đúng ý học viên."""
    require_speaking_access(current_student)
    question = _question(db, question_id)
    ideas = (payload.ideas or '').strip()
    if not ideas:
        raise HTTPException(status_code=400, detail="Please enter some ideas first.")

    try:
        data = await run_in_threadpool(I.answer_from_ideas, question.content,
                                       ideas[:MAX_ANSWER_CHARS], payload.target_band)
    except Exception as e:
        raise _ai_error(e)

    return {"question_id": question_id, "target_band": payload.target_band,
            "answer": data.get('answer'), "used_ideas": data.get('used_ideas') or [],
            "note": data.get('note')}


# ── Tự soạn bài mẫu (feedback 23/09) ───────────────────────────────────────────────────
# Học viên gõ sẵn một bài mẫu rồi mới luyện nói. Ba nút trong khung soạn: "Improve văn bản"
# (4 mức band), "Sửa lỗi từ vựng & ngữ pháp", và "Lưu làm bài mẫu" (lưu qua endpoint câu mẫu
# có sẵn ở speaking_analysis, không cần đường riêng).
#
# Bản feedback ghi rõ: KHUNG SOẠN BÀI VÀ CÁC CHỨC NĂNG AI BÊN TRONG LÀ TÍNH NĂNG VIP. Khác
# hẳn §6.4/§6.5 — những cái đó mở cho cả tài khoản thường — nên chặn ngay tại đây chứ không
# để giao diện tự ẩn nút.

MAX_DRAFT_CHARS = 4000
COMPOSE_VIP_MSG = "The model-answer composer and its AI tools are a VIP feature."


def _require_compose_vip(db: Session, user: User) -> None:
    from app.routes.student.speaking_test import is_vip
    from app.utils.speaking_gate import unlimited_for_testing
    if not (is_vip(user, db) or unlimited_for_testing(user)):
        raise HTTPException(status_code=403, detail=COMPOSE_VIP_MSG)


class ComposeIn(BaseModel):
    text: str
    # Vùng bôi đen, tính bằng CHỈ SỐ KÝ TỰ trong `text`. Bỏ trống = sửa cả bài. Dùng chỉ số
    # chứ không gửi đoạn chữ: một đoạn có thể xuất hiện nhiều lần, ghép nhầm chỗ là hỏng bài.
    start: Optional[int] = None
    end: Optional[int] = None
    target_band: str = '6.0-6.5'


def _draft(payload: ComposeIn) -> str:
    text = (payload.text or '').strip()
    if not text:
        raise HTTPException(status_code=400, detail="The composer is empty.")
    return text[:MAX_DRAFT_CHARS]


async def _run_compose(db, user, fn, *args) -> dict:
    """Trừ lượt trước, gọi AI, hỏng thì HOÀN lại lượt vừa trừ."""
    row = Q.take(db, user)
    try:
        data = await run_in_threadpool(fn, *args)
    except Exception as e:
        if row is not None:
            row.used = max(0, row.used - 1)
            db.commit()
        raise _ai_error(e)
    return {**data, "quota": Q.quota(db, user)}


@router.post("/speaking/practice/questions/{question_id}/compose/improve", response_model=dict)
async def compose_improve(question_id: int, payload: ComposeIn,
                          current_student: User = Depends(get_current_student),
                          db: Session = Depends(get_db)):
    """Nâng bài học viên tự soạn lên band mục tiêu; bôi đen thì chỉ nâng đoạn được chọn."""
    require_speaking_access(current_student)
    _require_compose_vip(db, current_student)
    question = _question(db, question_id)
    text = _draft(payload)
    data = await _run_compose(db, current_student, I.improve_draft, question.content,
                              text, payload.start, payload.end, payload.target_band)
    return {"question_id": question_id, "original": text, **data}


@router.post("/speaking/practice/questions/{question_id}/compose/fix", response_model=dict)
async def compose_fix(question_id: int, payload: ComposeIn,
                      current_student: User = Depends(get_current_student),
                      db: Session = Depends(get_db)):
    """Chỉ sửa lỗi từ vựng và ngữ pháp, giữ nguyên ý học viên viết ra."""
    require_speaking_access(current_student)
    _require_compose_vip(db, current_student)
    question = _question(db, question_id)
    text = _draft(payload)
    data = await _run_compose(db, current_student, I.fix_language, question.content,
                              text, payload.start, payload.end)
    return {"question_id": question_id, "original": text, **data}


# ── §6.5 Luyện phát âm ─────────────────────────────────────────────────────────────────

def _practice_key(text: str) -> str:
    """Khoá cache cho câu học viên tự nhập. Cùng bảng `speaking_tts` với clip giám khảo:
    `cache_key` vốn là chuỗi phẳng nên thêm một loại clip không phải đổi lược đồ."""
    clean = ' '.join((text or '').split()).lower()
    return "practice:" + hashlib.sha256(clean.encode('utf-8')).hexdigest()[:40]


@router.get("/speaking/practice/model-audio")
async def model_audio(request: Request, text: str, voice: str = None,
                      natural: bool = False,
                      token: Optional[str] = None, db: Session = Depends(get_db)):
    """▶ Nghe mẫu một từ / cụm / câu bất kỳ (§6.5).

    Sinh một lần rồi lưu lại: cả lớp luyện cùng một từ thì chỉ tốn một lần gọi. Token đi
    theo query vì thẻ <audio> không gửi được header Authorization.
    """
    user = _user_from_token(request, token, db)
    require_speaking_access(user)

    spoken = ' '.join((text or '').split())[:MAX_PRACTICE_CHARS]
    if not spoken:
        raise HTTPException(status_code=400, detail="There is nothing to read yet.")
    voice = voice if voice in voice_names() else default_voice()

    # Bản đọc tự nhiên (shadowing) là clip KHÁC bản đọc thường: chậm hơn và có ngắt nghỉ.
    # Khoá cache phải khác, nếu không hai bản đè lên nhau.
    key = _practice_key(('natural:' if natural else '') + spoken)
    # '|natural2' chứ không phải '|natural': bản đọc mẫu đã đổi sang giọng Chirp3-HD
    # (xem speaking_tts.NATURAL_VOICE), nên clip cũ trong cache phải được sinh lại.
    want = text_fingerprint(spoken + ('|natural2' if natural else ''), voice)
    row = db.query(SpeakingTts).filter(SpeakingTts.cache_key == key,
                                       SpeakingTts.voice == voice).first()
    if row is None or row.fingerprint != want or not row.audio:
        try:
            maker = natural_model_audio if natural else synthesize
            audio, ms = await run_in_threadpool(maker, spoken, voice)
        except Exception as e:
            logger.warning("Practice TTS failed: %s", e)
            raise HTTPException(status_code=503,
                                detail="The model reading is not available right now — please try again later.")
        if row is None:
            row = SpeakingTts(cache_key=key, voice=voice)
        row.fingerprint, row.audio, row.duration_ms = want, audio, ms
        db.add(row)
        db.commit()

    return Response(content=row.audio, media_type="audio/ogg",
                    headers={"Cache-Control": "private, max-age=86400",
                             "X-Duration-Ms": str(row.duration_ms or 0)})


@router.post("/speaking/practice/pronunciation", response_model=dict)
async def pronunciation(target_text: str = Form(...),
                        audio: UploadFile = File(...),
                        current_student: User = Depends(get_current_student),
                        db: Session = Depends(get_db)):
    """🎙️ Học viên đọc, AI chấm 0–100 (§6.5).

    Thang này CHỈ dành cho luyện phát âm và không bao giờ thay Band Pronunciation của
    IELTS — prompt nói rõ, và giao diện phải ghi rõ như vậy.
    """
    require_speaking_access(current_student)
    target = ' '.join((target_text or '').split())[:MAX_PRACTICE_CHARS]
    if not target:
        raise HTTPException(status_code=400, detail="There is nothing to practise yet.")

    raw, mime = await _read_audio(audio)

    # Im lặng thì dừng TRƯỚC khi trừ lượt: chưa gọi AI thì chưa tốn gì (xem cửa kiểm ở
    # app/utils/speaking_improve.py — lỗi "im lặng được 96 điểm" 19/09).
    silent = await run_in_threadpool(I.signal_problem, raw, _debug_tag(current_student, 'pron', target))
    if silent:
        return _no_score(db, current_student, target, silent)

    # Trước feedback 14/09 đường này KHÔNG đếm lượt: gọi Gemini trên audio bao nhiêu lần
    # cũng được. Giờ nó tiêu chung túi 10 lượt/ngày với Shadowing và bài học phát âm.
    Q.take(db, current_student)
    # Lượt nghe mù đã tốn một lần gọi AI, nên bản ghi không phải giọng người vẫn tính lượt —
    # không thì tiếng bíp thành cách gọi AI miễn phí vô hạn.
    wrong = await run_in_threadpool(I.speech_problem, target, raw, mime)
    if wrong:
        return _no_score(db, current_student, target, wrong)
    try:
        data = await run_in_threadpool(I.score_pronunciation, target, raw, mime)
    except Exception as e:
        Q.give_back(db, current_student)
        raise _ai_error(e)

    # Model được phép trả score null khi không nghe rõ — thà không có điểm còn hơn một con
    # số bịa ra, vì học viên sẽ luyện theo nó.
    score = data.get('score')
    try:
        score = max(0, min(100, int(round(float(score))))) if score is not None else None
    except (TypeError, ValueError):
        score = None

    return {"target_text": target, "score": score, "problem": data.get('problem'),
            "sounds": data.get('sounds') or [],
            # Trọng âm từ là một nửa của việc chấm phát âm (feedback 09/09), nên nó đi
            # kèm một câu kết luận riêng chứ không chỉ là danh sách từ sai.
            "word_stress": data.get('word_stress') or [],
            "word_stress_verdict": data.get('word_stress_verdict'),
            "clarity": data.get('clarity'), "tips": data.get('tips') or [],
            "quota": Q.quota(db, current_student),
            "scale_note": "This 0–100 scale is for pronunciation practice only — "
                          "it is not an IELTS Pronunciation band."}


# ── AI Shadowing (feedback 06/09) ──────────────────────────────────────────────────────

MAX_SHADOW_CHARS = 600          # một đoạn, không phải cả bài


@router.get("/speaking/practice/shadowing/quota", response_model=dict)
async def shadowing_quota(current_student: User = Depends(get_current_student),
                          db: Session = Depends(get_db)):
    """Còn mấy lượt hôm nay. Giao diện phải nói trước, chứ không để học viên ghi âm xong
    mới báo hết. Đường này giữ nguyên tên cũ cho giao diện khỏi phải đổi, nhưng con số
    trả về giờ là túi chung của cả ba chức năng luyện (app/utils/speaking_quota.py)."""
    require_speaking_access(current_student)
    q = Q.quota(db, current_student)
    db.commit()
    return q


@router.post("/speaking/practice/shadowing", response_model=dict)
async def shadowing(target_text: str = Form(...),
                    audio: UploadFile = File(...),
                    current_student: User = Depends(get_current_student),
                    db: Session = Depends(get_db)):
    """Nghe mẫu, nhắc lại, AI nhận xét. Tiêu chung túi 10 lượt/ngày với hai chức năng
    luyện kia (feedback 14/09)."""
    require_speaking_access(current_student)
    target = ' '.join((target_text or '').split())[:MAX_SHADOW_CHARS]
    if not target:
        raise HTTPException(status_code=400, detail="There is no passage to practise yet.")

    raw, mime = await _read_audio(audio)
    empty = {"dimensions": [], "summary": None}
    silent = await run_in_threadpool(I.signal_problem, raw, _debug_tag(current_student, 'shadow', target))
    if silent:
        q = Q.quota(db, current_student)
        return _no_score(db, current_student, target, silent, remaining=q['remaining'], **empty)
    Q.take(db, current_student)
    wrong = await run_in_threadpool(I.speech_problem, target, raw, mime)
    if wrong:
        q = Q.quota(db, current_student)
        return _no_score(db, current_student, target, wrong, remaining=q['remaining'], **empty)
    try:
        data = await run_in_threadpool(I.score_shadowing, target, raw, mime)
    except Exception as e:
        Q.give_back(db, current_student)
        raise _ai_error(e)

    score = data.get('score')
    try:
        score = max(0, min(100, int(round(float(score))))) if score is not None else None
    except (TypeError, ValueError):
        score = None

    by_key = {d.get('key'): d for d in (data.get('dimensions') or []) if isinstance(d, dict)}
    return {
        "target_text": target,
        "score": score,
        "problem": data.get('problem'),
        # Trả đủ năm chiều theo đúng thứ tự spec, kể cả chiều model bỏ qua — giao diện khỏi
        # phải đoán và học viên thấy được cái nào "ổn rồi".
        "dimensions": [{"key": k, **{kk: vv for kk, vv in (by_key.get(k) or {}).items()
                                    if kk != 'key'}} for k in I.SHADOW_DIMENSIONS],
        "summary": data.get('summary'),
        "quota": Q.quota(db, current_student),
        "remaining": Q.quota(db, current_student)['remaining'],
        "scale_note": "This 0–100 score is for shadowing practice only — "
                      "it is not an IELTS Pronunciation band.",
    }
