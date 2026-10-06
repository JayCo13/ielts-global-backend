"""Forecast Parts — luyện theo chủ đề dự đoán (docs/speaking-spec.md §7).

Khác Full Test ở đúng hai điểm, và đó là toàn bộ lý do màn này tồn tại: **không tổ hợp
đề** (học viên tự chọn chủ đề) và **không thi thử** (chỉ luyện tập). Vì thế không dùng
lại `build_plan` mà có `build_topic_plan` riêng — `build_plan` bốc ngẫu nhiên vài câu
trong chủ đề, mà ở đây học viên đã nhìn thấy danh sách câu trước khi bấm Bắt đầu, bốc
ngẫu nhiên là nói dối họ.

`is_active` ở đây mang đúng nghĩa gốc của nó: "còn hiện trong Forecast hay không".
Đây là chỗ DUY NHẤT được phép lọc theo cờ đó — chủ đề hết cửa sổ xuất hiện vẫn là đề hợp
lệ để thi, chỉ là không còn nằm trong danh sách dự đoán.
"""
import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (SpeakingAttempt, SpeakingAttemptAnswer, SpeakingQuestion,
                               SpeakingTopic, User)
from app.routes.admin.auth import get_current_student
from app.utils import speaking_grade as G
from app.utils.speaking_assemble import (NoMaterial, build_topic_plan, part3_questions,
                                         topic_questions)
from app.utils.speaking_gate import require_speaking_access, unlimited_for_testing
from app.utils.speaking_tts import default_voice, voice_names
# Quota, VIP và việc dựng sẵn các dòng câu trả lời phải dùng chung một nguồn với phòng
# thi Full Test; chép lại ở đây là sớm muộn hai bên lệch nhau.
from app.routes.student.speaking_test import (FREE_DAILY_ATTEMPTS, DAILY_LIMIT_MSG,
                                              _attempts_today, _materialise_answers,
                                              _now, grade_quota, is_vip)

logger = logging.getLogger(__name__)
router = APIRouter()

# Sáu nhóm chủ đề admin tick khi thêm đề Part 2 (cột `category`). Part 1 không chia nhóm.
CATEGORY_LABELS = [
    ('place', 'Places'),
    ('people', 'People'),
    ('education', 'Education'),
    ('recreation', 'Recreation'),
    ('object', 'Objects'),
    ('others', 'Others'),
]
SORTS = ('forecast', 'newest', 'title')


def _parse_month(value: Optional[str]):
    """'2026-12-01' → date. Tháng không hợp lệ thì bỏ qua bộ lọc thay vì báo lỗi — lọc là
    tiện ích, không đáng làm hỏng cả trang."""
    if not value:
        return None
    try:
        y, m, _d = value.split('-')
        return date(int(y), int(m), 1)
    except (ValueError, AttributeError):
        return None


def _band_of(scores: Optional[dict]) -> Optional[float]:
    """Điểm một câu = trung bình các tiêu chí đã chấm.

    Dùng lại `G.band_for_average` thay vì tự nghĩ cách quy đổi, để con số hiện ở danh sách
    này khớp với con số học viên thấy ở màn kết quả — kể cả khi hiệu chỉnh thang điểm được
    tắt đi bằng SPEAKING_CALIBRATION=0.
    """
    if not isinstance(scores, dict):
        return None
    vals = []
    for v in scores.values():
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if not vals:
        return None
    return G.band_for_average(sum(vals) / len(vals))


def _history(db: Session, user_id: int):
    """(số lần đã trả lời, điểm cao nhất) theo từng câu và theo từng chủ đề.

    Một lượt quét cho tất cả, vì trang này liệt kê hàng chục chủ đề — hỏi riêng từng
    chủ đề là mỗi lần mở trang tốn hàng chục truy vấn.
    """
    rows = (db.query(SpeakingAttemptAnswer.question_id, SpeakingAttemptAnswer.topic_id,
                     SpeakingAttemptAnswer.answer_status, SpeakingAttemptAnswer.scores)
            .join(SpeakingAttempt,
                  SpeakingAttempt.attempt_id == SpeakingAttemptAnswer.attempt_id)
            .filter(SpeakingAttempt.user_id == user_id,
                    SpeakingAttemptAnswer.is_ai_followup.is_(False))
            .all())
    by_q, by_topic = {}, {}
    for question_id, topic_id, status, scores in rows:
        # Câu bỏ trống không tính là "đã làm" — nếu tính thì nút "ẩn câu đã làm rồi" sẽ
        # giấu mất đúng những câu học viên cần quay lại.
        if status != 'answered':
            continue
        band = _band_of(scores)
        for key, bucket in ((question_id, by_q), (topic_id, by_topic)):
            if key is None:
                continue
            slot = bucket.setdefault(key, {'times': 0, 'best_band': None, 'best_scores': None})
            slot['times'] += 1
            if band is not None and (slot['best_band'] is None or band > slot['best_band']):
                slot['best_band'] = band
                # feedback 08/09: chỗ nào hiện điểm thì phải hiện cả 4 tiêu chí. Một con số
                # 5.5 trần trụi không nói được học viên mạnh yếu ở đâu.
                slot['best_scores'] = scores if isinstance(scores, dict) else None
    return by_q, by_topic


def _topic_row(topic: SpeakingTopic, counts: dict, hist: dict) -> dict:
    seen = hist.get(topic.topic_id) or {}
    return {
        "topic_id": topic.topic_id,
        "title": topic.title,
        "category": topic.category,
        "is_important": bool(topic.is_important),
        "forecast_level": topic.forecast_level,      # 1..4 sao, như 3 kỹ năng kia
        # Độ khó, cùng thiết kế với 3 kỹ năng kia. None = chưa đủ lượt để xếp hạng —
        # giao diện phải im lặng chứ đừng hiện "chưa rõ", nói vậy không giúp gì.
        "difficulty": topic.difficulty_label,
        "occurrence_count": topic.occurrence_count,
        "question_count": counts.get(topic.topic_id, 0),
        "times_practised": seen.get('times', 0),
        "best_band": seen.get('best_band'),
        "best_scores": seen.get('best_scores'),
        "done": bool(seen.get('times')),
    }


def _sorted(rows: List[dict], sort: str) -> List[dict]:
    if sort == 'title':
        return sorted(rows, key=lambda r: (r['title'] or '').lower())
    if sort == 'newest':
        return sorted(rows, key=lambda r: -r['topic_id'])
    # Mặc định: sao dự đoán cao trước, rồi số lần xuất hiện, rồi tên cho ổn định thứ tự.
    return sorted(rows, key=lambda r: (-(r['forecast_level'] or 0),
                                       -(r['occurrence_count'] or 0),
                                       (r['title'] or '').lower()))


def _grouped(rows: List[dict], sort: str) -> List[dict]:
    """Part 2 và Part 3 xếp theo 6 nhóm chủ đề (§7); nhóm rỗng thì không hiện."""
    out = []
    for key, label in CATEGORY_LABELS:
        chunk = [r for r in rows if (r['category'] or 'others') == key]
        if chunk:
            out.append({"key": key, "label": label, "topics": _sorted(chunk, sort)})
    return out


def _unlimited(db: Session, user) -> bool:
    """Không bị tính lượt THI: VIP Speaking hoặc tài khoản test. Cùng điều kiện với
    `speaking_test.test_setup`, để hai màn hình không nói hai con số khác nhau."""
    return is_vip(user, db) or unlimited_for_testing(user)


@router.get("/speaking/forecast", response_model=dict)
async def forecast_list(sort: str = 'forecast', hide_done: bool = False,
                        month: Optional[str] = None,
                        current_student: User = Depends(get_current_student),
                        db: Session = Depends(get_db)):
    """Ba mục Part 1 / Part 2 / Part 3 của danh sách dự đoán.

    `month` (YYYY-MM-DD, ngày đầu tháng) lọc theo tháng thi dự kiến — ngân hàng đề gắn cửa
    sổ xuất hiện, nên ai sắp thi tháng nào chỉ cần đúng chủ đề của tháng đó.
    """
    require_speaking_access(current_student)
    vip = is_vip(current_student, db)

    # §8: xếp theo dự đoán là tính năng VIP. Không báo lỗi mà lặng lẽ về mặc định khác —
    # người dùng thường vẫn xem được danh sách, chỉ là không sắp theo ⭐ được.
    locked = (sort == 'forecast' and not vip)
    if sort not in SORTS or locked:
        sort = 'newest' if locked else 'forecast'

    q = db.query(SpeakingTopic).filter(SpeakingTopic.is_active.is_(True))
    picked = _parse_month(month) if vip else None   # lọc theo tháng là quyền VIP
    # Tháng đã trôi qua thì bỏ qua bộ lọc: danh sách tháng ở giao diện cũng không còn hiện
    # nó, nhưng ai đó giữ link cũ vẫn phải thấy đề chứ không thấy trang trống.
    today = _now().date()
    if picked is not None and picked < date(today.year, today.month, 1):
        picked = None
    if picked is not None:
        # Chủ đề "của tháng X" = tháng X nằm trong cửa sổ xuất hiện của nó.
        q = q.filter(SpeakingTopic.appear_from.isnot(None),
                     SpeakingTopic.appear_to.isnot(None),
                     SpeakingTopic.appear_from <= picked,
                     SpeakingTopic.appear_to >= picked)
    topics = q.all()

    # Đếm câu theo từng part, vì "chủ đề Part 3" dùng chung dòng topic với Part 2 — chỉ
    # khác nhau ở part của câu hỏi. Một truy vấn gộp cho cả trang.
    per_part = {}
    for topic_id, part, n in (db.query(SpeakingQuestion.topic_id, SpeakingQuestion.part,
                                       func.count(SpeakingQuestion.question_id))
                              .group_by(SpeakingQuestion.topic_id,
                                        SpeakingQuestion.part).all()):
        per_part.setdefault(topic_id, {})[part] = n

    p1_counts = {t: v.get('part1', 0) for t, v in per_part.items()}
    p2_counts = {t: v.get('part2', 0) + v.get('part2_followup', 0)
                 for t, v in per_part.items()}
    p3_counts = {t: v.get('part3', 0) for t, v in per_part.items()}

    _, by_topic = _history(db, current_student.user_id)

    part1 = [_topic_row(t, p1_counts, by_topic) for t in topics
             if t.part == 'part1' and p1_counts.get(t.topic_id)]
    part2 = [_topic_row(t, p2_counts, by_topic) for t in topics
             if t.part == 'part2' and p2_counts.get(t.topic_id)]
    part3 = [_topic_row(t, p3_counts, by_topic) for t in topics
             if t.part == 'part2' and p3_counts.get(t.topic_id)]

    if hide_done:
        part1 = [r for r in part1 if not r['done']]
        part2 = [r for r in part2 if not r['done']]
        part3 = [r for r in part3 if not r['done']]

    return {
        "is_vip": vip,
        # HAI hạn mức, trả cả hai (chủ dự án phản ánh 21/09: màn Full Test hiện 0/1 còn
        # trang này hiện 1/1, trông như mâu thuẫn vì hai pill giống hệt nhau mà lại là hai
        # túi lượt khác nhau). Luyện theo đề dự đoán tiêu CHUNG cả túi lượt thi lẫn túi
        # lượt chấm với thi thử, nên phải thấy cả hai trước khi bỏ công nói cả chủ đề.
        **{k: v for k, v in grade_quota(db, current_student).items() if k != 'remaining'},
        "attempts_used": _attempts_today(db, current_student.user_id),
        "attempts_limit": None if _unlimited(db, current_student) else FREE_DAILY_ATTEMPTS,
        "attempts_remaining": (None if _unlimited(db, current_student) else
                               max(0, FREE_DAILY_ATTEMPTS
                                   - _attempts_today(db, current_student.user_id))),
        # feedback 08/09: chọn tháng dự đoán và xem phần sửa bài là quyền VIP.
        "month_locked": not vip,
        "analysis_locked": not vip,
        "sort": sort,
        "sort_locked": not vip,          # ⭐ sorting khoá với non-VIP (§7 + §8)
        "hide_done": hide_done,
        "month": picked.isoformat() if picked else None,
        "sections": {
            "part1": {"label": "Part 1", "topics": _sorted(part1, sort)},
            "part2": {"label": "Part 2", "groups": _grouped(part2, sort)},
            "part3": {"label": "Part 3", "groups": _grouped(part3, sort)},
        },
    }


@router.get("/speaking/forecast/topics/{topic_id}", response_model=dict)
async def forecast_topic(topic_id: int, section: str = 'part1',
                         current_student: User = Depends(get_current_student),
                         db: Session = Depends(get_db)):
    """Danh sách câu hỏi của một chủ đề — §7 bắt hiện danh sách TRƯỚC, rồi mới có nút
    Bắt đầu, và mỗi câu đã làm phải hiện số lần cùng điểm số."""
    require_speaking_access(current_student)
    if section not in ('part1', 'part2', 'part3'):
        raise HTTPException(status_code=400, detail="Invalid part.")
    topic = db.query(SpeakingTopic).filter(SpeakingTopic.topic_id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")

    questions = part3_questions(db, topic) if section == 'part3' else topic_questions(db, topic)
    by_q, by_topic = _history(db, current_student.user_id)
    seen = by_topic.get(topic_id) or {}

    items = []
    for q in questions:
        h = by_q.get(q.question_id) or {}
        items.append({
            "question_id": q.question_id,
            "part": q.part,
            # Part 2 hiện cue card đầy đủ, đúng thứ học viên sẽ nghe.
            "text": (topic.cue_card or q.content) if q.part == 'part2' else q.content,
            "times_practised": h.get('times', 0),
            "best_band": h.get('best_band'),
            "best_scores": h.get('best_scores'),
        })

    return {
        "topic_id": topic.topic_id,
        "title": topic.title,
        "section": section,
        # Nút "Xem sửa bài" mở trang phân tích từng câu — quyền VIP (feedback 08/09).
        "analysis_locked": not is_vip(current_student, db),
        # Khung "Tự soạn bài mẫu" mở được từ NGAY ĐÂY, kể cả câu chưa luyện (feedback
        # 28/09) — soạn trước rồi mới nói là đúng thứ tự học. Không dùng lại
        # `analysis_locked` dù hai giá trị đang bằng nhau: hai tính năng khác nhau thì phải
        # có cờ riêng, mai kia đổi quyền một bên là không kéo nhầm bên kia.
        "compose_locked": not is_vip(current_student, db),
        "category": topic.category,
        "is_important": bool(topic.is_important),
        "forecast_level": topic.forecast_level,
        "times_practised": seen.get('times', 0),
        "best_band": seen.get('best_band'),
        "questions": items,
    }


class StartForecast(BaseModel):
    topic_id: int
    section: str = 'part1'                  # part1 | part2 | part3
    question_id: Optional[int] = None       # luyện đúng một câu
    input_method: str = 'micro'
    voice: Optional[str] = None


@router.post("/speaking/forecast/start", response_model=dict)
async def start_forecast(payload: StartForecast,
                         current_student: User = Depends(get_current_student),
                         db: Session = Depends(get_db)):
    require_speaking_access(current_student)
    if payload.section not in ('part1', 'part2', 'part3'):
        raise HTTPException(status_code=400, detail="Invalid part.")

    vip = is_vip(current_student, db)
    # Cùng một hạn mức với Full Test: nếu miễn trừ ở đây thì §8 bị lách bằng cách luyện
    # từng chủ đề thay vì thi cả bài.
    if not vip and not unlimited_for_testing(current_student) \
            and _attempts_today(db, current_student.user_id) >= FREE_DAILY_ATTEMPTS:
        raise HTTPException(status_code=403, detail=DAILY_LIMIT_MSG)

    topic = db.query(SpeakingTopic).filter(
        SpeakingTopic.topic_id == payload.topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")

    voice = payload.voice if payload.voice in voice_names() else default_voice()
    try:
        plan = build_topic_plan(db, topic=topic, section=payload.section,
                                question_id=payload.question_id, voice=voice)
    except NoMaterial as e:
        raise HTTPException(status_code=409, detail=str(e))

    attempt = SpeakingAttempt(
        user_id=current_student.user_id,
        test_type=payload.section,
        mode='practice',
        input_method='subtitle' if payload.input_method == 'subtitle' else 'micro',
        voice=voice,
        use_forecast=True,
        exam_priority='default',
        plan=plan,
        status='in_progress',
        started_at=_now(),
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    _materialise_answers(db, attempt, plan)

    return {"attempt_id": attempt.attempt_id, "plan": plan, "is_vip": vip}
