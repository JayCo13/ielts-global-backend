"""Student: taking a Speaking test (docs/speaking-spec.md §4).

The room is thin on purpose. `POST /speaking/test/start` assembles the whole paper once
and freezes it; from then on the browser walks `plan.steps` and posts one answer at a
time. Nothing about *which* question comes next is decided server-side mid-test, so a
dropped connection or a reload resumes from the same paper instead of a different one.

Recordings: VN writes them to local disk outside `static/`. Global (Koyeb, ephemeral disk)
stores them privately in Cloudflare R2 under random keys (app/utils/speaking_storage.py).
The browser never gets an R2 URL; playback comes back through
`/speaking/test/recordings/{answer_id}`, which checks ownership and streams from R2.

Ported from the VN tree (ielts-main-nov). Student-facing strings are English.
"""
import logging
import os
from datetime import date
from typing import List, Optional

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, HTTPException,
                     Request, UploadFile, status)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (
    SpeakingAttempt, SpeakingAttemptAnswer, SpeakingSuggestion, SpeakingTopic,
    SpeakingTts, SpeakingVocabulary, User, VIPPackage, VIPSubscription,
)
from app.routes.admin.auth import get_current_student
from app.utils import speaking_audio, speaking_storage, speaking_stt
from app.utils.datetime_utils import get_vietnam_time
from app.utils.speaking_assemble import ASKED_KINDS, NoMaterial, build_plan
from app.utils.speaking_gate import require_speaking_access, unlimited_for_testing
from app.utils.speaking_tts import default_voice, voice_catalog, voice_names
from app.utils.vip_access import package_covers

logger = logging.getLogger(__name__)
router = APIRouter()

# Recordings live in R2 (private keys), not on disk: see the module docstring.
MAX_RECORDING_BYTES = 12 * 1024 * 1024   # a two-minute opus answer is ~250KB
FREE_DAILY_ATTEMPTS = 1                  # §8 — non-VIP gets one test a day

VIP_ONLY_FORECAST = "Choosing an exam month from the Forecast is a VIP feature."
# feedback 09/09: "Khóa thi full-test (nguyên bài)" cho tài khoản thường — cùng luật với
# Listening/Reading/Writing, ở đó bài nguyên đề cũng là quyền VIP. Tài khoản thường vẫn
# thi được từng Part và vẫn luyện được theo chủ đề dự đoán.
VIP_ONLY_FULL_TEST = ("The full test (Parts 1, 2 and 3) is a VIP feature. "
                      "You can still take each Part on its own.")
DAILY_LIMIT_MSG = (f"You have used today's Speaking test "
                   f"(free accounts get {FREE_DAILY_ATTEMPTS} test per day, reset at midnight). "
                   "Upgrade to VIP for unlimited tests.")


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def is_vip(user: User, db: Session) -> bool:
    """VIP Speaking CHỈ đến từ một gói lẻ có `skill_type='speaking'`.

    Gói 'all_skills' là gói cũ thời Writing/Speaking còn miễn phí nên chỉ gồm Listening +
    Reading, KHÔNG mở Speaking (app/utils/vip_access.py). Đừng gộp 'speaking' vào đó cho
    tiện: 472 thuê bao all_skills đang còn hạn sẽ có VIP Speaking miễn phí — đúng sai lầm
    đã mắc với Writing hồi 17/08.

    HAI LOẠI role 'student' được đối xử KHÁC NHAU (chủ dự án chốt 21/09):
      * học sinh THƯỜNG (không thuộc trung tâm nào) → VIP Speaking miễn phí, như mọi kỹ
        năng khác. Đây là tài khoản khoá học do admin cấp, vốn đã trả tiền ở chỗ khác.
      * HỌC SINH TRUNG TÂM (có dòng `CenterMembership` member_type='student') → PHẢI MUA
        gói Speaking. Họ vẫn VÀO được Speaking nhưng theo hạn mức tài khoản thường: một
        lượt thi và một lượt chấm mỗi ngày, chỉ điểm tổng quan, không thi nguyên đề.
        Trung tâm muốn mở cho học sinh thì mua gói Speaking qua ví trung tâm
        (center_wallet) — ở đó `package_covers` đã tính đúng.

    Tài khoản test được coi là VIP luôn. Trước đây chúng chỉ được miễn hạn mức, nên màn
    kết quả hiện "VIP · chấm không giới hạn" (suy từ `grade_limit`) mà ba tab Nhận xét,
    Phân tích và Xuất file vẫn khoá (suy từ `is_vip`) — không kiểm được đúng thứ đang cần
    kiểm, mà nhìn thì tưởng hệ thống hỏng. Gộp về đây thì cả hai chỗ nói cùng một điều.

    GỠ CÙNG LÚC VỚI SPEAKING_TESTERS khi Speaking mở cho học viên — nếu quên, ba tài
    khoản này sẽ vĩnh viễn có VIP Speaking miễn phí.
    """
    if unlimited_for_testing(user):
        return True
    if user.role == "student" and not is_centre_student(db, user):
        return True
    return db.query(VIPSubscription.subscription_id).join(VIPPackage).filter(
        VIPSubscription.user_id == user.user_id,
        VIPSubscription.end_date >= _now(),
        VIPSubscription.payment_status == "completed",
        package_covers("speaking"),
    ).first() is not None


def is_centre_student(db: Session, user: User) -> bool:
    """Tài khoản này có phải HỌC SINH CỦA MỘT TRUNG TÂM không.

    Phân biệt bằng bảng `center_memberships`, không phải bằng role: role 'student' gồm cả
    học sinh khoá học do admin cấp (không thuộc trung tâm nào) lẫn học sinh trung tâm.
    Đo 21/09: 82 tài khoản role 'student' đang hoạt động, 0 người thuộc trung tâm — nên
    hiện tại nhánh này chưa ảnh hưởng ai, nhưng sẽ đúng ngay khi trung tâm thêm học sinh.

    Tính cả dòng đang tạm dừng / vô hiệu hoá: họ vẫn là người của trung tâm, và trung tâm
    mới là bên quyết định mua gói cho họ.
    """
    from app.models.models import CenterMembership
    return db.query(CenterMembership.membership_id).filter(
        CenterMembership.user_id == user.user_id,
        CenterMembership.member_type == 'student').first() is not None


def free_attempt_id(db: Session, user: User) -> Optional[int]:
    """Bài Speaking ĐẦU TIÊN đã nộp của tài khoản, hoặc None nếu chưa nộp bài nào.

    Tính theo bài ĐÃ NỘP chứ không phải bài đã bấm Bắt đầu: bỏ dở một lần rồi mới thi thật
    là chuyện thường, mà tính cả bài bỏ dở thì học viên mất luôn suất xem thử mà không hiểu
    vì sao. Xếp theo attempt_id để kết quả không đổi giữa hai lần gọi.
    """
    row = (db.query(SpeakingAttempt.attempt_id)
           .filter(SpeakingAttempt.user_id == user.user_id,
                   SpeakingAttempt.submitted.is_(True))
           .order_by(SpeakingAttempt.attempt_id.asc()).first())
    return row[0] if row else None


def free_question_ids(db: Session, user: User) -> set:
    """Các câu hỏi thuộc bài được xem thử — phân tích từng câu mở đúng những câu này."""
    aid = free_attempt_id(db, user)
    if not aid:
        return set()
    return {qid for (qid,) in db.query(SpeakingAttemptAnswer.question_id).filter(
        SpeakingAttemptAnswer.attempt_id == aid).all() if qid}


def has_full_access(db: Session, user: User, attempt=None) -> bool:
    """VIP xem được mọi bài; tài khoản thường được xem đầy đủ ĐÚNG MỘT bài đầu tiên.

    Feedback 23/09 mở lại ngoại lệ đã bỏ ngày 21/09, nhưng chỉ cho BÀI ĐẦU TIÊN: người mới
    phải nhìn thấy Nhận xét và Phân tích ít nhất một lần thì mới biết mình đang mua gì.
    Từ bài thứ hai trở đi khoá lại như cũ.

    Gọi mà không kèm `attempt` thì không có bài nào để đối chiếu, nên chỉ VIP mới qua.
    """
    if is_vip(user, db):
        return True
    if attempt is None:
        return False
    return attempt.attempt_id == free_attempt_id(db, user)


def _attempts_today(db: Session, user_id: int) -> int:
    """Counts attempts started today whatever their outcome. An abandoned test still
    consumed a slot — otherwise the daily limit is bypassed by quitting each time."""
    start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.count(SpeakingAttempt.attempt_id)).filter(
        SpeakingAttempt.user_id == user_id,
        SpeakingAttempt.started_at >= start).scalar() or 0


def _parse_month(text: Optional[str]) -> Optional[date]:
    """'08/2026' or '2026-08' -> the first of that month."""
    if not text:
        return None
    digits = [int(p) for p in ''.join(c if c.isdigit() else ' ' for c in text).split()]
    if len(digits) < 2:
        return None
    a, b = digits[0], digits[1]
    month, year = (a, b) if a <= 12 else (b, a)
    if not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="Invalid exam month.")
    return date(year, month, 1)


def _user_from_token(request: Request, token: Optional[str], db: Session) -> User:
    """Resolve the caller from the Authorization header, falling back to a `token` query
    parameter.

    Three callers cannot send a header: an <audio> element has no way to set one, and
    navigator.sendBeacon — used to close out a test when the tab is closed — does not
    accept headers either. The same arrangement is already used by the listening review
    player.
    """
    from jose import jwt, JWTError
    from app.routes.admin.auth import SECRET_KEY, ALGORITHM

    if not token:
        auth = request.headers.get("authorization", "")
        token = auth.replace("Bearer ", "") if auth else None
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        username = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


# ── Setup screen (§4.1) ────────────────────────────────────────────────────────────────

@router.get("/speaking/test/setup", response_model=dict)
async def test_setup(current_student: User = Depends(get_current_student),
                     db: Session = Depends(get_db)):
    """Everything the pre-test screen needs: the student's remaining quota, the voices,
    and which exam months actually have forecast material behind them."""
    require_speaking_access(current_student)
    # `unlimited` gộp VIP và tài khoản test: cả hai đều không bị tính lượt. Bản trước chỉ
    # miễn ở ba chỗ CHẶN mà quên chỗ này, nên máy chủ vẫn trả `can_start=false` và giao
    # diện khoá nút kèm câu "Bạn đã dùng hết lượt thi hôm nay" — chặn thì hết rồi mà nút
    # thì vẫn không bấm được.
    vip = is_vip(current_student, db)
    unlimited = vip or unlimited_for_testing(current_student)
    used = _attempts_today(db, current_student.user_id)

    windows = db.query(SpeakingTopic.appear_from, SpeakingTopic.appear_to).filter(
        SpeakingTopic.is_active.is_(True),
        SpeakingTopic.appear_from.isnot(None),
        SpeakingTopic.appear_to.isnot(None)).all()
    # Chỉ tháng hiện tại trở đi (feedback 08/09). Tháng đã qua thì dù ngân hàng còn đề dự
    # đoán, chọn nó cũng vô nghĩa — không ai đi thi vào một tháng đã trôi qua.
    today = _now().date()
    this_month = date(today.year, today.month, 1)
    months = set()
    for start, end in windows:
        cursor = date(start.year, start.month, 1)
        while cursor <= end:
            if cursor >= this_month:
                months.add(cursor.isoformat())
            cursor = date(cursor.year + (cursor.month == 12),
                          1 if cursor.month == 12 else cursor.month + 1, 1)

    return {
        "is_vip": vip,
        "attempts_used": used,
        "attempts_limit": None if unlimited else FREE_DAILY_ATTEMPTS,
        "attempts_remaining": None if unlimited else max(0, FREE_DAILY_ATTEMPTS - used),
        # Trả kèm hạn mức CHẤM để màn này và trang đề dự đoán hiện cùng một bộ pill —
        # trước đây mỗi màn chỉ hiện một túi lượt nên nhìn như hai con số mâu thuẫn.
        **{k: v for k, v in grade_quota(db, current_student).items() if k != 'remaining'},
        "can_start": unlimited or used < FREE_DAILY_ATTEMPTS,
        # Giao diện khoá sẵn lựa chọn "Thi nguyên bài" thay vì để bấm rồi mới báo lỗi.
        "full_test_locked": not unlimited,
        "voices": voice_catalog(),
        "default_voice": default_voice(),
        "forecast_months": sorted(months),
        "forecast_locked": not vip,      # choosing an exam month is VIP-only (§8)
    }


# ── Starting a test ────────────────────────────────────────────────────────────────────

class StartTest(BaseModel):
    test_type: str = 'full'          # full | part1 | part2 | part3
    mode: str = 'practice'           # practice | mock
    input_method: str = 'micro'      # micro | subtitle
    occupation: Optional[str] = None  # student | working
    voice: Optional[str] = None
    use_forecast: bool = False
    forecast_month: Optional[str] = None
    priority: str = 'default'        # default | done | undone


def _serialise_answer(a: SpeakingAttemptAnswer) -> dict:
    return {
        "answer_id": a.answer_id,
        "order_index": a.order_index,
        "part": a.part,
        "question_id": a.question_id,
        "status": a.answer_status,
        "used_retry": a.used_retry,
        "text": a.final_text,
        "has_audio": bool(a.final_audio),
        # §9: ghi âm cũ bị dọn nhưng lịch sử ở lại. Không có cờ này thì một câu đã trả lời
        # trông y hệt câu chưa từng nói.
        "audio_expired": bool(a.audio_expired),
        "duration_ms": a.duration_ms,
    }


def _materialise_answers(db: Session, attempt: SpeakingAttempt, plan: dict):
    """One row per question up front, all starting at 'no answer'.

    Creating them lazily on submit would leave a skipped question with no row at all, and
    §4.6 wants an explicit status for every question that was asked — including the ones
    the student said nothing to.
    """
    for step in plan['steps']:
        if step['kind'] not in ASKED_KINDS:
            continue
        db.add(SpeakingAttemptAnswer(
            attempt_id=attempt.attempt_id,
            question_id=step.get('question_id'),
            topic_id=step.get('topic_id'),
            part=step['part'],
            order_index=step['order_index'],
            topic_title=step.get('topic_title'),
            question_text=step.get('text'),
            is_ai_followup=step['kind'] == 'ai_followup',
        ))
    db.commit()


@router.post("/speaking/test/start", response_model=dict)
async def start_test(payload: StartTest,
                     current_student: User = Depends(get_current_student),
                     db: Session = Depends(get_db)):
    require_speaking_access(current_student)
    if payload.test_type not in ('full', 'part1', 'part2', 'part3'):
        raise HTTPException(status_code=400, detail="Invalid test type.")
    if payload.mode not in ('practice', 'mock'):
        raise HTTPException(status_code=400, detail="Invalid mode.")

    vip = is_vip(current_student, db)
    unlimited = vip or unlimited_for_testing(current_student)
    if payload.test_type == 'full' and not unlimited:
        raise HTTPException(status_code=403, detail=VIP_ONLY_FULL_TEST)
    # §8: the block sits on the Start button, before any AI or assembly work is done.
    if not unlimited \
            and _attempts_today(db, current_student.user_id) >= FREE_DAILY_ATTEMPTS:
        raise HTTPException(status_code=403, detail=DAILY_LIMIT_MSG)

    month = None
    if payload.use_forecast:
        if not unlimited:
            raise HTTPException(status_code=403, detail=VIP_ONLY_FORECAST)
        month = _parse_month(payload.forecast_month)
        if not month:
            raise HTTPException(status_code=400, detail="Please choose your expected exam month.")

    voice = payload.voice if payload.voice in voice_names() else default_voice()
    try:
        plan = build_plan(db, user_id=current_student.user_id,
                          test_type=payload.test_type, mode=payload.mode,
                          occupation=payload.occupation, voice=voice,
                          forecast_month=month,
                          priority=payload.priority if payload.priority in
                          ('default', 'done', 'undone') else 'default')
    except NoMaterial as e:
        # A forecast month with nothing behind it gets its own wording (§4.1); anything
        # else means the bank itself is short.
        detail = ("There is no Forecast material for the month you chose yet." if month else str(e))
        raise HTTPException(status_code=409, detail=detail)

    attempt = SpeakingAttempt(
        user_id=current_student.user_id,
        test_type=payload.test_type,
        mode=payload.mode,
        input_method='subtitle' if payload.input_method == 'subtitle' else 'micro',
        occupation=payload.occupation if payload.occupation in ('student', 'working') else None,
        voice=voice,
        use_forecast=bool(month),
        forecast_month=month,
        exam_priority=plan['priority'],
        plan=plan,
        status='in_progress',
        started_at=_now(),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    _materialise_answers(db, attempt, plan)

    return {"attempt_id": attempt.attempt_id, "plan": plan, "is_vip": vip}


@router.post("/speaking/test/attempts/{attempt_id}/retake", response_model=dict)
async def retake_attempt(attempt_id: int,
                         current_student: User = Depends(get_current_student),
                         db: Session = Depends(get_db)):
    """§6.1 "Thi lại đúng bộ câu hỏi cũ" — dựng bài MỚI từ đề đã đóng băng.

    Phải là một bài mới chứ không phải mở lại bài cũ: bài cũ đã `completed`, mọi câu nộp
    vào đó đều bị chặn 409 (và đó đúng là lỗi mà bản đầu mắc phải — phòng thi mở ra được
    nhưng không nộp nổi câu nào). Điểm bài gốc vì thế cũng không hề bị đụng tới, đúng như
    spec đòi: "Kết quả bài thi" và "Kết quả Retake" là hai thứ tách bạch.
    """
    require_speaking_access(current_student)
    source = _own_attempt(db, attempt_id, current_student)
    if not source.plan:
        raise HTTPException(status_code=409, detail="This test no longer has a paper to retake.")

    vip = is_vip(current_student, db)
    if not vip and not unlimited_for_testing(current_student) \
            and _attempts_today(db, current_student.user_id) >= FREE_DAILY_ATTEMPTS:
        raise HTTPException(status_code=403, detail=DAILY_LIMIT_MSG)

    attempt = SpeakingAttempt(
        user_id=current_student.user_id,
        test_type=source.test_type,
        # Thi lại luôn là luyện tập: học viên đã biết trước đề, bấm giờ như thi thật nữa
        # thì con số thu được không nói lên điều gì.
        mode='practice',
        input_method=source.input_method,
        occupation=source.occupation,
        voice=source.voice,
        use_forecast=source.use_forecast,
        forecast_month=source.forecast_month,
        exam_priority=source.exam_priority,
        plan=source.plan,
        status='in_progress',
        started_at=_now(),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    _materialise_answers(db, attempt, source.plan)

    return {"attempt_id": attempt.attempt_id, "plan": source.plan, "is_vip": vip,
            "retake_of": attempt_id}


GRADE_FEATURE = 'grade'
FREE_GRADES_PER_DAY = 1          # feedback 07/09: giống Writing


def _grade_usage(db: Session, user_id: int):
    """Dòng đếm lượt chấm của hôm nay. Dùng chung bảng với AI Shadowing."""
    from app.models.models import SpeakingDailyUsage
    today = _now().date()
    row = db.query(SpeakingDailyUsage).filter(
        SpeakingDailyUsage.user_id == user_id,
        SpeakingDailyUsage.day == today,
        SpeakingDailyUsage.feature == GRADE_FEATURE).first()
    if row is None:
        row = SpeakingDailyUsage(user_id=user_id, day=today,
                                 feature=GRADE_FEATURE, used=0)
        db.add(row)
        db.flush()
    return row


def grade_quota(db: Session, user: User) -> dict:
    """`remaining = None` nghĩa là KHÔNG giới hạn (VIP) — khác hẳn 0 là đã hết lượt.
    Giao diện phải phân biệt được hai cái đó."""
    # Tài khoản test bỏ qua hạn mức giống VIP — xem `unlimited_for_testing`.
    if is_vip(user, db) or unlimited_for_testing(user):
        return {"grade_limit": None, "grade_used": 0, "grade_remaining": None,
                "remaining": None}
    row = _grade_usage(db, user.user_id)
    left = max(0, FREE_GRADES_PER_DAY - row.used)
    return {"grade_limit": FREE_GRADES_PER_DAY, "grade_used": row.used,
            "grade_remaining": left, "remaining": left}


def _own_attempt(db: Session, attempt_id: int, user: User) -> SpeakingAttempt:
    attempt = db.query(SpeakingAttempt).filter(
        SpeakingAttempt.attempt_id == attempt_id,
        SpeakingAttempt.user_id == user.user_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Test not found.")
    return attempt


@router.get("/speaking/test/attempts/{attempt_id}", response_model=dict)
async def get_attempt(attempt_id: int,
                      current_student: User = Depends(get_current_student),
                      db: Session = Depends(get_db)):
    """Resume, or read back a finished test. The plan is returned as stored, so a paper
    never changes shape between the start of a test and the end of it."""
    require_speaking_access(current_student)
    attempt = _own_attempt(db, attempt_id, current_student)
    answers = db.query(SpeakingAttemptAnswer).filter(
        SpeakingAttemptAnswer.attempt_id == attempt_id).order_by(
        SpeakingAttemptAnswer.order_index).all()
    return {
        "attempt_id": attempt.attempt_id,
        "status": attempt.status,
        "submitted": attempt.submitted,
        "mode": attempt.mode,
        "input_method": attempt.input_method,
        "voice": attempt.voice,
        "test_type": attempt.test_type,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
        "overall_band": attempt.overall_band,
        "plan": attempt.plan,
        "answers": [_serialise_answer(a) for a in answers],
    }


# ── Examiner audio ─────────────────────────────────────────────────────────────────────

@router.get("/speaking/test/audio")
async def examiner_audio(request: Request, key: str, voice: str = None,
                         token: Optional[str] = None, db: Session = Depends(get_db)):
    """Play one pre-generated examiner clip.

    Same query-string token arrangement as the admin player and the listening review
    player: an <audio> element cannot send an Authorization header.
    """
    user = _user_from_token(request, token, db)
    require_speaking_access(user)

    row = db.query(SpeakingTts).filter(SpeakingTts.cache_key == key,
                                       SpeakingTts.voice == (voice or default_voice())).first()
    if not row or not row.audio:
        # The room falls back to showing the line as text; a missing clip must not end
        # the test (§4.1 treats every audio failure as recoverable).
        raise HTTPException(status_code=404, detail="No audio for this line yet")
    return Response(content=row.audio, media_type="audio/ogg",
                    headers={"Cache-Control": "private, max-age=86400",
                             "X-Duration-Ms": str(row.duration_ms or 0)})


# ── Submitting one answer ──────────────────────────────────────────────────────────────

def _store_recording(attempt_id: int, answer_id: int, retry: bool,
                     data: bytes, filename: str):
    """Compress to Opus, then upload to R2. Returns (key_or_None, encoded_bytes), or None
    for an empty blob — normal whenever the student says nothing.

    The key is None when R2 is not configured or the upload failed: the answer (and its
    Whisper transcript) is still saved, only the audio is missing. Never write to local
    disk here — on Koyeb it would vanish on the next redeploy.

    Calls ffmpeg and the R2 API, so it MUST run outside the event loop (see the caller).
    """
    if not data:
        return None
    if len(data) > MAX_RECORDING_BYTES:
        raise HTTPException(status_code=413, detail="The recording is too large.")
    encoded, opus_ext = speaking_audio.to_opus(data)
    ext = opus_ext
    if not ext:
        ext = os.path.splitext(filename or "")[1].lower()
        if ext not in ('.webm', '.ogg', '.m4a', '.mp4', '.mp3', '.wav'):
            ext = '.webm'
    key = speaking_storage.recording_key(attempt_id, answer_id, retry, ext)
    if not speaking_storage.save(key, encoded):
        key = None
    return key, encoded


@router.post("/speaking/test/attempts/{attempt_id}/answers", response_model=dict)
async def submit_answer(attempt_id: int,
                        order_index: int = Form(...),
                        answer_status: str = Form('answered'),
                        duration_ms: Optional[int] = Form(None),
                        text: Optional[str] = Form(None),
                        is_retry: bool = Form(False),
                        audio: Optional[UploadFile] = File(None),
                        current_student: User = Depends(get_current_student),
                        db: Session = Depends(get_db)):
    """Save one answer — a recording, typed text in Subtitle Mode, or neither.

    Called once per question as the test runs rather than in one batch at the end, so that
    a browser crash mid-test still leaves everything answered so far.
    """
    require_speaking_access(current_student)
    attempt = _own_attempt(db, attempt_id, current_student)
    if attempt.status != 'in_progress':
        raise HTTPException(status_code=409, detail="This test has already ended.")

    answer = db.query(SpeakingAttemptAnswer).filter(
        SpeakingAttemptAnswer.attempt_id == attempt_id,
        SpeakingAttemptAnswer.order_index == order_index).first()
    if not answer:
        raise HTTPException(status_code=404, detail="Question not found in this test.")
    if answer_status not in ('answered', 'no_answer', 'auto_skipped', 'time_expired'):
        answer_status = 'answered'
    # Only Practice Mode offers a second go (§4.5); in Mock the system does not intervene.
    retry = bool(is_retry) and attempt.mode == 'practice'

    transcript = (text or '').strip() or None
    if audio is not None:
        raw = await audio.read()
        filename = audio.filename or "answer.webm"
        # ffmpeg và Whisper đều là lời gọi CHẶN. Gọi thẳng trong một route async sẽ giữ
        # cả worker uvicorn trong lúc chờ — đúng cái đã làm phòng thi treo một phút hồi
        # 28/08. Cả hai phải chạy trong threadpool.
        stored = await run_in_threadpool(
            _store_recording, attempt_id, answer.answer_id, retry, raw, filename)
        if stored:
            path, data = stored
            if path:
                if retry:
                    answer.retry_audio = path
                else:
                    answer.first_audio = path
            # Subtitle Mode đã có sẵn chữ; chỉ bản ghi mới cần Whisper.
            if not transcript:
                transcript = await run_in_threadpool(
                    speaking_stt.transcribe, data, filename)

    if retry:
        answer.retry_text = transcript
        answer.used_retry = True
    else:
        answer.first_text = transcript
    answer.answer_status = answer_status
    if duration_ms is not None:
        answer.duration_ms = int(duration_ms)
    db.commit()

    # Practice Mode nudge — a suggestion, never a requirement (§4.5).
    too_short = (attempt.mode == 'practice' and not retry
                 and answer_status == 'answered'
                 and speaking_stt.looks_too_short(transcript, answer.part))
    return {"answer": _serialise_answer(answer), "suggest_retry": too_short}


@router.get("/speaking/test/recordings/{answer_id}")
async def get_recording(request: Request, answer_id: int, retry: bool = False,
                        token: Optional[str] = None, db: Session = Depends(get_db)):
    """Play back one stored answer. Ownership is checked here because the recordings sit
    in R2 under private random keys precisely so they cannot be fetched without it."""
    user = _user_from_token(request, token, db)

    answer = db.query(SpeakingAttemptAnswer).join(
        SpeakingAttempt,
        SpeakingAttempt.attempt_id == SpeakingAttemptAnswer.attempt_id).filter(
        SpeakingAttemptAnswer.answer_id == answer_id,
        SpeakingAttempt.user_id == user.user_id).first()
    if not answer:
        raise HTTPException(status_code=404, detail="Recording not found.")
    path = answer.retry_audio if retry else (answer.final_audio or answer.first_audio)
    # R2 read is a blocking network call — keep it off the event loop.
    data = await run_in_threadpool(speaking_storage.load, path) if path else None
    if not data:
        # §9 prunes old audio while keeping the metadata forever, so a missing file is a
        # normal outcome the player is expected to explain, not an error.
        raise HTTPException(status_code=404, detail="This recording is no longer stored.")
    mime = speaking_storage.mime_for(path, 'audio/webm')
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "private, max-age=3600"})


# ── The 💡 Gợi ý button (§4.2, Practice Mode only) ─────────────────────────────────────

@router.get("/speaking/test/questions/{question_id}/help", response_model=dict)
async def question_help(question_id: int,
                        current_student: User = Depends(get_current_student),
                        db: Session = Depends(get_db)):
    """The outline, band-level model answers and vocabulary an admin generated for this
    question (§2.3).

    Nothing here calls the AI — §6.8 is explicit that this material is prepared when the
    question is added, and only actions the student deliberately triggers may spend
    tokens. Opening the hint panel is not one of them.
    """
    require_speaking_access(current_student)
    suggestion = db.query(SpeakingSuggestion).filter(
        SpeakingSuggestion.question_id == question_id).first()
    vocab = db.query(SpeakingVocabulary).filter(
        SpeakingVocabulary.question_id == question_id).order_by(
        SpeakingVocabulary.band_level, SpeakingVocabulary.order_index).all()
    by_band = {}
    for v in vocab:
        by_band.setdefault(v.band_level, []).append(
            {"term": v.term, "meaning_vi": v.meaning_vi, "example": v.example})
    # Feedback 06/09: trong phòng luyện phải thấy được câu mẫu MÌNH đã nói lần trước, để
    # bấm lên xem rồi nói lại. Chỉ có khi đã làm câu này rồi — lần đầu thì không hiện gì.
    # Vẫn không gọi AI: đây là dữ liệu đã chấm từ trước.
    from app.routes.student.speaking_analysis import (_answers_for_question, pick_sample,
                                                      _band as _answer_band)
    from app.models.models import SpeakingQuestionProgress
    prog = db.query(SpeakingQuestionProgress).filter(
        SpeakingQuestionProgress.user_id == current_student.user_id,
        SpeakingQuestionProgress.question_id == question_id).first()
    previous = None
    if prog is not None and prog.sample_text:
        previous = {"text": prog.sample_text, "band": None, "from_ai": True,
                    "label": "Your model answer (written by AI)"}
    else:
        history = _answers_for_question(db, current_student.user_id, question_id)
        picked, manual = pick_sample(history, prog.sample_answer_id if prog else None)
        if picked is not None and (picked.final_text or '').strip():
            previous = {"text": picked.final_text, "band": _answer_band(picked.scores),
                        "from_ai": False,
                        "label": "Your chosen model answer" if manual
                                 else "Your best answer"}

    return {
        "question_id": question_id,
        "outline": suggestion.outline if suggestion else None,
        "samples": suggestion.samples if suggestion else None,
        "vocabulary": by_band,
        "previous_best": previous,
        # An admin may not have generated anything yet; the panel says so rather than
        # appearing empty.
        "available": bool(suggestion or vocab or previous),
    }


# ── The seventh Part 3 question (§4.3) ────────────────────────────────────────────────

AI_FOLLOWUP_SYSTEM = (
    "You are the examiner in an IELTS Speaking Part 3 discussion. Write ONE follow-up "
    "question that digs deeper into what this candidate has just said.\n"
    "Rules:\n"
    "- It must follow naturally from their own answers, not restate a question already asked.\n"
    "- Keep it a genuine Part 3 question: abstract, general, about society or trends — "
    "never a personal question about the candidate's own life.\n"
    # Feedback 15/09: "Câu follow up đang khó quá, mình cho prompt vào đây thêm 1 tiêu chí
    # là 'không hỏi quá khó'." Câu này viết ra từ chính bài nói của học viên, nên model cứ
    # bám theo ý hay nhất họ vừa nói rồi đẩy lên một tầng trừu tượng nữa — thành ra câu
    # cuối khó hơn hẳn sáu câu ngân hàng đứng trước nó, và học viên tịt ngay ở câu chốt.
    "- Do NOT make it harder than the questions already asked. Aim at a band 6 candidate: "
    "everyday wording, one idea only, answerable in two or three sentences without any "
    "specialist knowledge.\n"
    "- Avoid academic or abstract jargon, multi-part questions ('and how far...?'), and "
    "anything that needs statistics, history or policy detail to answer.\n"
    "- One sentence, the way an examiner speaks it aloud. No preamble, no numbering.\n"
    'Return JSON: {"question": "..."}'
)
# Said if the model is unreachable: a generic Part 3 probe still fits any topic, and the
# spec is firm that the test always has seven questions.
AI_FOLLOWUP_FALLBACK = "And how do you think this might change in the future?"


@router.post("/speaking/test/attempts/{attempt_id}/ai-followup", response_model=dict)
async def ai_followup(attempt_id: int,
                      current_student: User = Depends(get_current_student),
                      db: Session = Depends(get_db)):
    """Write the last Part 3 question from what the student actually said.

    Called by the room when it reaches the `ai_followup` step. The wording is stored on
    the answer row and echoed back into the frozen plan, so a reload asks the same
    question rather than improvising a second one.
    """
    require_speaking_access(current_student)
    attempt = _own_attempt(db, attempt_id, current_student)
    answer = db.query(SpeakingAttemptAnswer).filter(
        SpeakingAttemptAnswer.attempt_id == attempt_id,
        SpeakingAttemptAnswer.is_ai_followup.is_(True)).first()
    if not answer:
        raise HTTPException(status_code=404, detail="This test has no AI follow-up question.")
    if answer.question_text:
        return {"question": answer.question_text, "audio_key": None, "cached": True}

    said = db.query(SpeakingAttemptAnswer).filter(
        SpeakingAttemptAnswer.attempt_id == attempt_id,
        SpeakingAttemptAnswer.part == 'part3',
        SpeakingAttemptAnswer.is_ai_followup.is_(False)).order_by(
        SpeakingAttemptAnswer.order_index).all()
    transcript = "\n\n".join(
        f"Q: {a.question_text}\nA: {a.final_text}" for a in said if a.final_text)

    question = None
    try:
        from app.utils import gemini_client
        payload = gemini_client.generate_json(
            AI_FOLLOWUP_SYSTEM,
            f"Topic: {answer.topic_title}\n\nThe discussion so far:\n"
            f"{transcript or '(the candidate has said very little)'}",
            temperature=0.7)
        question = ' '.join((payload.get('question') or '').split()) or None
    except Exception as e:
        logger.warning("Speaking AI follow-up failed (attempt %s): %s", attempt_id, e)
    question = question or AI_FOLLOWUP_FALLBACK

    answer.question_text = question
    db.commit()

    # This one line was never in the bank, so its clip is made now. TTS failing is not
    # fatal — the room reads the question out as text instead.
    audio_key = f"followup:{answer.answer_id}"
    try:
        from app.utils import speaking_tts
        voice = attempt.voice or default_voice()
        blob, ms = speaking_tts.synthesize(question, voice)
        db.add(SpeakingTts(cache_key=audio_key, voice=voice,
                           fingerprint=speaking_tts.text_fingerprint(question, voice),
                           audio=blob, duration_ms=ms))
        db.commit()
    except Exception as e:
        logger.warning("Speaking AI follow-up TTS failed (attempt %s): %s", attempt_id, e)
        db.rollback()
        audio_key = None

    return {"question": question, "audio_key": audio_key, "cached": False}


# ── Finishing ──────────────────────────────────────────────────────────────────────────

class FinishTest(BaseModel):
    status: str = 'completed'      # completed | abandoned | terminated | interrupted
    reason: Optional[str] = None


@router.post("/speaking/test/attempts/{attempt_id}/finish", response_model=dict)
async def finish_test(attempt_id: int, payload: FinishTest, request: Request,
                      background: BackgroundTasks,
                      token: Optional[str] = None,
                      db: Session = Depends(get_db)):
    """End a test. Only a `completed` one is eligible for marking (§4.4) — quitting,
    three silent questions in a row, leaving the screen, or a crash all keep the history
    but must never reach the AI.

    Closing the tab has to end the test too, and that arrives via sendBeacon, which is why
    this route resolves the caller itself instead of taking the usual dependency.
    """
    user = _user_from_token(request, token, db)
    require_speaking_access(user)
    attempt = _own_attempt(db, attempt_id, user)
    if attempt.status != 'in_progress':
        return {"status": attempt.status, "submitted": attempt.submitted,
                "gradable": attempt.status == 'completed' and attempt.submitted}

    if payload.status not in ('completed', 'abandoned', 'terminated', 'interrupted'):
        raise HTTPException(status_code=400, detail="Invalid finish status.")

    # Nộp bài phải qua đúng cửa chống chia sẻ tài khoản như Listening/Reading/Writing
    # (feedback 14/09). Chỉ chặn lúc NỘP THẬT: bốn trạng thái kia đến từ sendBeacon lúc
    # đóng tab hay mất mạng — chặn ở đó thì bài kẹt mãi ở 'in_progress' và học viên mất
    # luôn lượt, mà cũng chẳng ngăn được ai chia sẻ tài khoản.
    if payload.status == 'completed':
        from app.routes.admin.auth import check_multiple_sessions
        session_token = token or request.headers.get(
            'authorization', '').replace('Bearer ', '')
        if check_multiple_sessions(db, user.user_id, session_token):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Multiple active sessions detected. This test has been cancelled and "
                       "this device will be signed out. If account sharing continues, the "
                       "account will be permanently banned automatically without admin "
                       "review.")
    attempt.status = payload.status
    attempt.end_reason = (payload.reason or '')[:255] or None
    attempt.submitted = payload.status == 'completed'
    attempt.ended_at = _now()
    db.commit()

    # KHÔNG chấm ở đây nữa (feedback 07/09). Nộp bài xong ra thẳng màn kết quả để học viên
    # đọc lại đề và bài mình đã nói; chấm chỉ chạy khi họ chủ động bấm, và mỗi lần bấm trừ
    # một lượt. Chấm tự động nghĩa là mọi bài bỏ dở nửa chừng, mọi bài thi thử cho vui đều
    # tốn một lần gọi AI có trả tiền.
    return {"status": attempt.status, "submitted": attempt.submitted,
            "gradable": attempt.status == 'completed'}


def _grade_in_background(attempt_id: int):
    from app.jobs.speaking_grade import run as run_grading
    try:
        run_grading(attempt_id)
    except Exception:
        # Job đã tự ghi grade_status='failed' và log; nuốt ở đây để BackgroundTasks
        # không nhả traceback trần vào log mỗi lần AI trục trặc.
        logger.warning("Speaking grading for attempt %s failed", attempt_id)


SUBTITLE_WARNING = ("This test was taken in typing (subtitle) mode, so there is no recording: "
                    "Pronunciation cannot be assessed, and each part band is based on the "
                    "other 3 criteria.")


def _without_internal(part_results):
    """Bỏ `internal` (con số làm việc của model, vd 6.3) khỏi bản trả về.

    Tài liệu chấm điểm v2 mục 6: các giá trị lẻ "chỉ được sử dụng internally", điểm tiêu
    chí người dùng thấy là số nguyên. Vẫn giữ trong DB để đối chiếu model nghiêng lên ở
    đâu, chỉ không đưa ra trình duyệt.
    """
    out = []
    for part in part_results or []:
        criteria = {key: {k: v for k, v in (block or {}).items() if k != 'internal'}
                    for key, block in (part.get('criteria') or {}).items()}
        out.append({**part, 'criteria': criteria})
    return out


def _overview_only(part_results):
    """Bản rút gọn cho tài khoản thường: CHỈ điểm, không một chữ nhận xét nào.

    Feedback 09/09 tách hẳn hai thứ: "chấm điểm" ra điểm cho đề và bài — ai cũng được;
    còn nhận xét chung, phân tích và xuất file là của VIP. Bản trước vẫn kèm
    `general_comment`, tức là tài khoản thường vẫn đọc được phần đáng tiền nhất.

    Cắt ở máy chủ chứ không ẩn ở giao diện — dữ liệu đã trả về trình duyệt thì coi như
    đã lộ. Với bài chấm sau ngày này thì phần nhận xét cũng không hề được sinh ra
    (jobs/speaking_grade.py), đây là lớp chặn thứ hai cho các bài chấm cũ.
    """
    slim = []
    for part in part_results or []:
        criteria = {key: {'band': block.get('band')}
                    for key, block in (part.get('criteria') or {}).items()}
        slim.append({**part, 'criteria': criteria,
                     'note': part.get('note')})
    return slim


@router.get("/speaking/test/attempts/{attempt_id}/result", response_model=dict)
async def attempt_result(attempt_id: int,
                         current_student: User = Depends(get_current_student),
                         db: Session = Depends(get_db)):
    """Bản chấm của một bài.

    Trả về cả khi chưa chấm xong — màn kết quả hỏi lại theo chu kỳ, nên `grade_status`
    quan trọng ngang bản thân điểm số.
    """
    require_speaking_access(current_student)
    attempt = _own_attempt(db, attempt_id, current_student)
    vip = is_vip(current_student, db)
    # Tài khoản thường: mở đầy đủ đúng BÀI ĐẦU TIÊN (feedback 23/09), các bài sau chỉ điểm.
    full = has_full_access(db, current_student, attempt)
    has_audio = attempt.input_method != 'subtitle'

    answers = db.query(SpeakingAttemptAnswer).filter(
        SpeakingAttemptAnswer.attempt_id == attempt_id).order_by(
        SpeakingAttemptAnswer.order_index).all()

    parts = attempt.part_results or []
    return {
        "attempt_id": attempt.attempt_id,
        "status": attempt.status,
        "submitted": attempt.submitted,
        # Chưa nộp hoàn chỉnh thì không bao giờ có điểm (§4.4) — nói thẳng ra đây để màn
        # kết quả khỏi chờ mãi một bản chấm không bao giờ tới.
        "gradable": attempt.status == 'completed' and attempt.submitted,
        "grade_status": attempt.grade_status,
        "grade_error": attempt.grade_error if attempt.grade_status == 'failed' else None,
        # Đã chờ bao lâu, tính ở MÁY CHỦ. Nút "Dừng" chỉ được hiện khi đủ lâu, mà nếu để
        # trình duyệt tự đếm thì tải lại trang là đếm lại từ 0 — bài kẹt mười phút vẫn
        # không bao giờ ló ra cái nút. Đồng hồ máy học viên lệch cũng không ảnh hưởng.
        "grade_waited_seconds": (
            int((_now() - attempt.graded_at).total_seconds())
            if attempt.graded_at and attempt.grade_status in ('pending', 'running')
            else None),
        "grade_cancel_after": _cancel_after(attempt),
        "test_type": attempt.test_type,
        "mode": attempt.mode,
        "input_method": attempt.input_method,
        # Để màn kết quả biết quay về đâu: bài luyện theo dự đoán về trang dự đoán, bài thi
        # thử về màn chuẩn bị.
        "is_forecast_practice": bool((attempt.plan or {}).get('forecast_topic_id')),
        # §7 — bài lẻ một part không có Overall, chỉ có Part Band.
        "has_overall": attempt.test_type == 'full',
        "overall_band": attempt.overall_band,
        "criteria": attempt.criteria or {},
        "parts": _without_internal(parts) if full else _overview_only(parts),
        "is_vip": vip,
        # Đang mở nhờ suất xem thử bài đầu tiên chứ không phải nhờ VIP — màn kết quả nói rõ
        # để học viên không tưởng mình đã có VIP rồi ngạc nhiên ở bài thứ hai.
        "free_preview": bool(full and not vip),
        "detail_locked": not full,
        # feedback 07/09 + 09/09: tài khoản thường chỉ xem được ĐIỂM. Nhận xét từng tiêu
        # chí, phân tích từng câu và xuất PDF/Word đều là của VIP. Máy chủ quyết định,
        # không để giao diện tự ẩn.
        "feedback_locked": not full,
        "analysis_locked": not full,
        "export_locked": not full,
        # Bài đã chấm nhưng chỉ có điểm (chấm lúc tài khoản chưa VIP). VIP mở lên xem thì
        # tab Nhận xét sẽ rỗng, nên màn kết quả mời chấm lại thay vì hiện một trang trắng.
        "has_feedback": attempt.grade_error != 'scores_only',
        **{k: v for k, v in grade_quota(db, current_student).items() if k != 'remaining'},
        "warning": None if has_audio else SUBTITLE_WARNING,
        "answers": [{
            **_serialise_answer(a),
            "topic_title": a.topic_title,
            "question_text": a.question_text,
            "scores": a.scores,
            "feedback": a.feedback if full else None,
        } for a in answers],
    }


@router.post("/speaking/test/attempts/{attempt_id}/grade", response_model=dict)
async def regrade(attempt_id: int, background: BackgroundTasks,
                  current_student: User = Depends(get_current_student),
                  db: Session = Depends(get_db)):
    """Chấm bài — CHỈ chạy khi học viên chủ động bấm (feedback 07/09).

    Mỗi lần chấm là ba lượt gọi AI trên audio, nên nó tiêu lượt:
      * tài khoản thường: 1 lượt/ngày, đúng như Writing;
      * VIP: không giới hạn.

    Bài đang chấm dở mà quá `STUCK_AFTER` cũng vào được đây (tiến trình chết giữa chừng thì
    dòng nằm mãi ở 'running' và học viên không có đường nào thoát ra).
    """
    require_speaking_access(current_student)
    attempt = _own_attempt(db, attempt_id, current_student)
    if attempt.status != 'completed' or not attempt.submitted:
        raise HTTPException(status_code=409, detail="This test cannot be graded.")

    # 'running' cũng phải mở lại được. Nếu tiến trình chấm chết giữa chừng — máy chủ khởi
    # động lại, worker bị giết — dòng nằm mãi ở 'running': màn kết quả hỏi lại 5 giây một
    # lần đến vô tận và học viên không có nút nào thoát ra. Đã xảy ra thật trên prod, bài
    # số 37 kẹt từ 31/08. Job tự chống chấm chồng bằng chính STUCK_AFTER này, nên ở đây chỉ
    # cần đủ cũ là cho chạy lại.
    from app.jobs.speaking_grade import STUCK_AFTER   # nạp muộn như _grade_in_background
    stale = (attempt.grade_status == 'running'
             and (attempt.graded_at is None or _now() - attempt.graded_at >= STUCK_AFTER))
    # Bài đã chấm rồi thì thôi — TRỪ một trường hợp: bài chỉ mới có điểm (chấm lúc tài
    # khoản chưa VIP) mà giờ đã là VIP. Lúc đó chấm lại là cách duy nhất để sinh phần nhận
    # xét, và nó vẫn tiêu một lượt như mọi lần chấm khác.
    wants_feedback = (attempt.grade_error == 'scores_only'
                      and has_full_access(db, current_student, attempt))
    if attempt.grade_status == 'done' and not wants_feedback:
        return {"grade_status": "done", **grade_quota(db, current_student)}
    if attempt.grade_status == 'running' and not stale:
        return {"grade_status": "running", **grade_quota(db, current_student)}

    # Global: refuse cleanly BEFORE taking a grading turn when Gemini is not configured —
    # otherwise the job fails instantly and a free account loses its only daily grade.
    from app.utils import gemini_client
    if not gemini_client.is_configured():
        raise HTTPException(status_code=503,
                            detail="AI grading is not available right now. Please try again later.")

    quota = grade_quota(db, current_student)
    if quota['remaining'] is not None and quota['remaining'] <= 0:
        raise HTTPException(
            status_code=429,
            detail="You have used today's Speaking grading. "
                   "Upgrade to VIP for unlimited grading.")

    # Trừ lượt TRƯỚC khi xếp việc: bấm nhanh hai lần mà trừ sau là lọt hai lượt.
    if quota['remaining'] is not None:
        row = _grade_usage(db, current_student.user_id)
        row.used += 1
    attempt.grade_status = 'pending'
    attempt.grade_error = None
    # Đóng mốc "bấm chấm lúc mấy giờ" ngay từ 'pending'. Trước đây `graded_at` chỉ được ghi
    # khi job ĐÃ CHẠY, nên một bài kẹt ở 'pending' (tiến trình chết trước khi job kịp khởi
    # động) không có mốc nào để đo — không biết nó kẹt 5 giây hay 5 phút thì không dựng nổi
    # nút Dừng ở dưới. Job chạy sẽ ghi đè bằng mốc của chính nó.
    attempt.graded_at = _now()
    db.commit()
    background.add_task(_grade_in_background, attempt_id)
    return {"grade_status": "pending", **grade_quota(db, current_student)}


# Chờ đủ lâu rồi mới cho bấm Dừng — hiện sớm thì học viên huỷ đúng lúc mọi thứ vẫn chạy
# bình thường, mà bấm Dừng là bỏ một lần gọi AI đã trả tiền. Ngắn hơn hẳn STUCK_AFTER
# (20 phút) của job: con số kia để máy tự dọn rác, con số này để người ngồi đợi có lối ra.
#
# Tách theo loại bài (feedback 19/09: "Full test nó chấm cỡ 5 phút lận"). Bản 15/09 dùng
# chung 3 phút, nên với Full Test nút Dừng hiện ra GIỮA lúc đang chấm bình thường — mời
# học viên huỷ một lần chấm sắp xong. Full Test = ba lần gọi AI nối nhau, chừa biên cho
# lúc Gemini báo bận và phải chờ lùi.
CANCEL_AFTER_SECONDS = 180            # bài lẻ một Part: một lần gọi AI
CANCEL_AFTER_FULL_SECONDS = 480       # Full Test: ba lần gọi, ~5 phút khi mọi thứ ổn


def _cancel_after(attempt: SpeakingAttempt) -> int:
    return CANCEL_AFTER_FULL_SECONDS if attempt.test_type == 'full' else CANCEL_AFTER_SECONDS


@router.post("/speaking/test/attempts/{attempt_id}/grade/cancel", response_model=dict)
async def cancel_grade(attempt_id: int,
                       current_student: User = Depends(get_current_student),
                       db: Session = Depends(get_db)):
    """Dừng một lần chấm đang treo, và HOÀN lại lượt vừa trừ.

    Feedback 15/09: "Chấm bài full test, thỉnh thoảng bị lỗi, chạy hoài mà k chấm được.
    Nếu trường hợp đó xảy ra, thì mình có thể bấm dừng. Chấm lại."

    Trước đây đường thoát duy nhất là chờ hết `STUCK_AFTER` = 20 phút. Với tài khoản
    thường — một lượt chấm mỗi ngày — thì một lần treo là mất trắng cả ngày, vì lượt đã bị
    trừ lúc bấm còn bản chấm thì không bao giờ tới.

    Hoàn lượt chứ không chỉ mở khoá trạng thái: học viên đã trả một lượt cho thứ họ không
    nhận được, đúng nguyên tắc `speaking_quota.give_back`. Chống lạm dụng bằng
    `CANCEL_AFTER_SECONDS` — bấm chấm rồi huỷ ngay để nhân đôi lượt thì không lọt.
    """
    require_speaking_access(current_student)
    attempt = _own_attempt(db, attempt_id, current_student)
    if attempt.grade_status not in ('pending', 'running'):
        # Đã xong hoặc đã hỏng: không còn gì để dừng. Trả 200 kèm trạng thái thật để màn
        # kết quả tự vẽ lại — hai tab cùng mở một bài thì tab chậm chân không phải ăn lỗi.
        return {"grade_status": attempt.grade_status, "cancelled": False,
                **grade_quota(db, current_student)}

    # `graded_at` rỗng nghĩa là bài NÀY CHƯA TỪNG được xếp hàng chấm — 'pending' chỉ là
    # trạng thái mặc định của mọi bài vừa nộp. Không có lượt nào bị trừ thì cũng không có
    # gì để hoàn; bỏ qua vế này là tặng không một lượt cho mỗi bài chưa chấm.
    if attempt.graded_at is None:
        return {"grade_status": attempt.grade_status, "cancelled": False,
                **grade_quota(db, current_student)}

    waited = (_now() - attempt.graded_at).total_seconds()
    limit = _cancel_after(attempt)
    if waited < limit:
        raise HTTPException(
            status_code=409,
            detail="Your test is still being graded — please wait a little longer. Only "
                   f"stop it if it is still not done after {limit // 60} minutes.")

    attempt.grade_status = 'failed'
    attempt.grade_error = 'cancelled'
    # Hoàn lượt. VIP và tài khoản test không bị trừ nên cũng không có gì để hoàn.
    if grade_quota(db, current_student)['remaining'] is not None:
        row = _grade_usage(db, current_student.user_id)
        row.used = max(0, row.used - 1)
    db.commit()
    # Job cũ (nếu còn sống) vẫn có thể chạy nốt rồi ghi 'done' đè lên — đó là kết quả thật,
    # cứ để nó về. Job mới do cú "Chấm lại" xếp ra cũng ghi đè bằng bản của nó; cả hai đều
    # là bản chấm đầy đủ của cùng một bài nên ai về sau cũng đúng.
    return {"grade_status": "failed", "cancelled": True,
            **grade_quota(db, current_student)}


def _display_band(a: SpeakingAttempt) -> Optional[float]:
    """Overall nếu có; bài chỉ một Part thì Part Band của Part đó; chưa chấm thì None."""
    if a.grade_status != 'done':
        return None
    if a.overall_band is not None:
        return a.overall_band
    bands = [p.get('band') for p in (a.part_results or [])
             if isinstance(p, dict) and p.get('band') is not None]
    return bands[0] if len(bands) == 1 else None


@router.get("/speaking/test/attempts", response_model=List[dict])
async def list_attempts(limit: int = 20, offset: int = 0, kind: Optional[str] = None,
                        current_student: User = Depends(get_current_student),
                        db: Session = Depends(get_db)):
    """Lịch sử bài Speaking, mới nhất trước.

    `kind` = 'mock' (thi thử) | 'forecast' (luyện theo dự đoán) | bỏ trống = tất cả.
    Lọc ở ĐÂY chứ không để giao diện lọc: bản trước lấy 40 bài chung rồi mới lọc và cắt 8
    ở trình duyệt, nên nhánh nào ít bài hơn thì hụt hẳn, và không có cách xem bài cũ hơn
    (feedback 19/09: "lịch sử nên xem hết, hiện chỉ xem được 6 bài gần nhất").
    Cờ nằm trong cột JSON `plan` nên lọc bằng Python; mỗi học viên chỉ vài chục tới vài
    trăm bài, không đáng một cột mới.
    """
    require_speaking_access(current_student)
    rows = db.query(SpeakingAttempt).filter(
        SpeakingAttempt.user_id == current_student.user_id).order_by(
        SpeakingAttempt.attempt_id.desc()).all()
    if kind in ('mock', 'forecast'):
        want = kind == 'forecast'
        rows = [a for a in rows
                if bool((a.plan or {}).get('forecast_topic_id')) == want]
    offset = max(0, offset)
    rows = rows[offset:offset + max(1, min(limit, 100))]
    return [{
        "attempt_id": a.attempt_id,
        "test_type": a.test_type,
        "mode": a.mode,
        "input_method": a.input_method,
        "status": a.status,
        "submitted": a.submitted,
        "overall_band": a.overall_band,
        # Con số để HIỆN ở danh sách. Bài lẻ một Part và bài luyện dự đoán cố ý không có
        # Overall (§7), nên trước đây lịch sử của chúng toàn dấu "–" dù đã chấm xong
        # (feedback 19/09). Lúc đó điểm của chính Part duy nhất là con số đúng để hiện.
        "display_band": _display_band(a),
        "grade_status": a.grade_status,
        # 'pending' là mặc định của MỌI bài chưa ai bấm chấm — không phải "đang chấm".
        # Bản trước gắn chữ "đang chấm" cho cả những bài đó (ảnh feedback 19/09 có cả loạt
        # "Đang làm dở · đang chấm"). Đang chấm thật = job đang chạy, hoặc đã xếp hàng
        # (có mốc `graded_at`, đặt từ lúc bấm chấm).
        "grading": a.grade_status == 'running'
                   or (a.grade_status == 'pending' and a.graded_at is not None),
        "started_at": a.started_at.isoformat() if a.started_at else None,
        "ended_at": a.ended_at.isoformat() if a.ended_at else None,
        # Hai nhánh có lịch sử RIÊNG: thi thử và luyện theo chủ đề dự đoán. Bài luyện mang
        # theo tên chủ đề trong plan nên hiện được ngay, khỏi phải tra thêm bảng.
        "is_forecast_practice": bool((a.plan or {}).get('forecast_topic_id')),
        "topic_title": (a.plan or {}).get('forecast_topic_title'),
        "question_count": (a.plan or {}).get('question_count'),
    } for a in rows]
