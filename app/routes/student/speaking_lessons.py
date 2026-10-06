"""Pronunciation Lessons — nhánh thứ tư của Speaking (feedback 09/09).

Mỗi Unit có hai nửa:
  1. LÝ THUYẾT  — admin viết tay, chỉ để đọc.
  2. LUYỆN TẬP  — AI sinh từ chính lý thuyết đó: từ thì luyện phát âm, câu thì shadowing.

Không dựng cơ chế chấm mới: từ đi qua `speaking_improve.score_pronunciation`, câu đi qua
`score_shadowing` — đúng hai bộ prompt đang dùng ở trang phân tích. Chấm cùng một thứ bằng
hai bộ prompt khác nhau thì cùng một câu sẽ ra hai kết quả và học viên không biết tin cái
nào.

Hạn mức riêng cho nhánh này: 10 lượt luyện mỗi ngày với tài khoản thường, VIP không giới
hạn (feedback 09/09). Đếm chung cho cả hai kiểu vì với học viên nó chỉ là "một lượt luyện".
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import (SpeakingPronItem, SpeakingPronUnit,
                               User)
from app.routes.admin.auth import get_current_student
from app.routes.student.speaking_practice import _ai_error, _debug_tag, _read_audio
from app.utils import speaking_improve as I
from app.utils import speaking_quota as Q
from app.utils.speaking_gate import require_speaking_access

logger = logging.getLogger(__name__)
router = APIRouter()

# Feedback 14/09: tài khoản thường chỉ mở SÁU bài đầu, các bài sau khoá. Đếm theo thứ tự
# hiển thị (order_index) chứ không theo unit_id: admin xếp lại bài thì "sáu bài đầu" phải
# là sáu bài đầu học viên nhìn thấy, không phải sáu bài được tạo sớm nhất.
FREE_UNITS = 6
# Feedback 21/09 đổi luật: tài khoản thường ĐƯỢC XEM mọi bài (lý thuyết + bộ từ/câu),
# chỉ KHÔNG được luyện (bấm đọc để AI chấm) ở các bài từ thứ 7 trở đi. Trước đây bài thứ 7
# trở đi khoá hẳn cả phần xem.
VIP_ONLY_UNIT = ("Practising this lesson is a VIP feature. Free accounts can practise the "
                 f"first {FREE_UNITS} lessons and still read the content of the others.")

# Trần độ dài văn bản gửi kèm audio cho AI chấm. Mục luyện tập đã bị cắt ở 500 ký tự lúc
# sinh (`_clean_items`), nên con số này chỉ là chốt chặn cuối — bằng MAX_SHADOW_CHARS bên
# speaking_practice.py cho cùng loại việc.
#
# Hằng số này từng bị xoá mất trong lần gom hạn mức 14/09 mà dòng dùng nó ở `score_item`
# vẫn còn, nên MỌI lượt "Bấm để đọc" đều NameError → 500 sau 0,1 giây. Lỗi 500 thoát ra
# ngoài lớp CORS nên trình duyệt chỉ thấy "Failed to fetch" (feedback 19/09).
MAX_TARGET_CHARS = 600

# Cùng bị xoá sót trong lần đó: tài khoản thường gọi thẳng API làm mới nhận 500 thay vì
# 403 có lời giải thích.
VIP_ONLY_REFRESH = ("Refreshing the practice set is a VIP feature. "
                    "You can still practise the default set.")

# Hạn mức giờ dùng chung với luyện phát âm và AI Shadowing: một túi 10 lượt mỗi ngày
# cho tài khoản thường (app/utils/speaking_quota.py, feedback 14/09). Trước đây mỗi
# chỗ đếm một bảng riêng nên tiêu ba nơi là ba túi.


def _practice_locked(db: Session, user: User, unit: SpeakingPronUnit) -> bool:
    """Bài này có bị khoá phần LUYỆN với tài khoản thường không (xem thì luôn được)."""
    if Q.is_unlimited(db, user):
        return False
    rank = (db.query(func.count(SpeakingPronUnit.unit_id))
            .filter(SpeakingPronUnit.is_published.is_(True),
                    (SpeakingPronUnit.order_index < unit.order_index)
                    | ((SpeakingPronUnit.order_index == unit.order_index)
                       & (SpeakingPronUnit.unit_id < unit.unit_id)))
            .scalar() or 0)
    return rank >= FREE_UNITS


def _require_unit_practisable(db: Session, user: User, unit: SpeakingPronUnit):
    """Chặn ngay ở máy chủ khi CHẤM. Giao diện có ẩn nút hay không cũng chỉ là gợi ý: gõ
    thẳng id của mục luyện tập là gọi được AI."""
    if _practice_locked(db, user, unit):
        raise HTTPException(status_code=403, detail=VIP_ONLY_UNIT)


def _unit(db: Session, unit_id: int) -> SpeakingPronUnit:
    unit = db.query(SpeakingPronUnit).filter(
        SpeakingPronUnit.unit_id == unit_id).first()
    if not unit or not unit.is_published:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    return unit


def _items(db: Session, unit_id: int, user_id: int) -> List[SpeakingPronItem]:
    """Bộ RIÊNG của học viên nếu họ đã bấm làm mới, không thì bộ mặc định của Unit.

    Không trộn hai bộ: học viên bấm làm mới là muốn một bài khác hẳn, thấy lẫn bộ cũ vào
    thì không hiểu nút đó vừa làm gì.
    """
    mine = (db.query(SpeakingPronItem)
            .filter(SpeakingPronItem.unit_id == unit_id,
                    SpeakingPronItem.user_id == user_id)
            .order_by(SpeakingPronItem.order_index, SpeakingPronItem.item_id).all())
    if mine:
        return mine
    return (db.query(SpeakingPronItem)
            .filter(SpeakingPronItem.unit_id == unit_id,
                    SpeakingPronItem.user_id.is_(None))
            .order_by(SpeakingPronItem.order_index, SpeakingPronItem.item_id).all())


def _serialise(item: SpeakingPronItem) -> dict:
    return {"item_id": item.item_id, "kind": item.kind,
            "content": item.content, "note": item.note}


@router.get("/speaking/lessons", response_model=dict)
async def list_units(current_student: User = Depends(get_current_student),
                     db: Session = Depends(get_db)):
    """Danh sách Unit đã xuất bản, kèm số mục luyện tập của mỗi Unit."""
    require_speaking_access(current_student)
    units = (db.query(SpeakingPronUnit)
             .filter(SpeakingPronUnit.is_published.is_(True))
             .order_by(SpeakingPronUnit.order_index, SpeakingPronUnit.unit_id).all())

    counts = dict(db.query(SpeakingPronItem.unit_id,
                           func.count(SpeakingPronItem.item_id))
                  .filter(SpeakingPronItem.user_id.is_(None))
                  .group_by(SpeakingPronItem.unit_id).all())
    mine = dict(db.query(SpeakingPronItem.unit_id,
                         func.count(SpeakingPronItem.item_id))
                .filter(SpeakingPronItem.user_id == current_student.user_id)
                .group_by(SpeakingPronItem.unit_id).all())

    unlimited = Q.is_unlimited(db, current_student)
    return {
        "units": [{"unit_id": u.unit_id, "title": u.title,
                   "practice_count": mine.get(u.unit_id) or counts.get(u.unit_id, 0),
                   # Bài nào cũng vào xem được; từ bài thứ 7 tài khoản thường chỉ mất
                   # phần luyện (feedback 21/09).
                   # Xem thì luôn được; cờ này chỉ nói phần LUYỆN có bị khoá không.
                   "practice_locked": not unlimited and i >= FREE_UNITS,
                   "locked": False}
                  for i, u in enumerate(units)],
        "free_units": None if unlimited else FREE_UNITS,
        "quota": Q.quota(db, current_student),
        "refresh_locked": not unlimited,
    }


@router.get("/speaking/lessons/{unit_id}", response_model=dict)
async def unit_detail(unit_id: int,
                      current_student: User = Depends(get_current_student),
                      db: Session = Depends(get_db)):
    require_speaking_access(current_student)
    unit = _unit(db, unit_id)
    # KHÔNG chặn ở đây nữa (feedback 21/09): tài khoản thường xem được mọi bài, chỉ không
    # luyện được từ bài thứ 7. Giao diện dựa vào `practice_locked` để khoá nút đọc.
    items = _items(db, unit_id, current_student.user_id)
    return {
        "unit_id": unit.unit_id,
        "title": unit.title,
        "theory": unit.theory,
        "items": [_serialise(i) for i in items],
        # Bộ đang xem là của riêng học viên hay bộ mặc định — để giao diện nói rõ.
        "personalised": bool(items and items[0].user_id),
        "quota": Q.quota(db, current_student),
        "refresh_locked": not Q.is_unlimited(db, current_student),
        "practice_locked": _practice_locked(db, current_student, unit),
        "practice_locked_message": VIP_ONLY_UNIT,
        "free_units": None if Q.is_unlimited(db, current_student) else FREE_UNITS,
    }


@router.post("/speaking/lessons/{unit_id}/refresh", response_model=dict)
async def refresh_items(unit_id: int,
                        current_student: User = Depends(get_current_student),
                        db: Session = Depends(get_db)):
    """🔄 Làm mới nội dung luyện tập — quyền VIP (feedback 09/09).

    Chỉ thay bộ CỦA HỌC VIÊN NÀY. Ghi đè bộ mặc định thì một người bấm làm mới là cả lớp
    đổi bài, và admin mất luôn bộ mình đã duyệt.
    """
    require_speaking_access(current_student)
    if not Q.is_unlimited(db, current_student):
        raise HTTPException(status_code=403, detail=VIP_ONLY_REFRESH)
    unit = _unit(db, unit_id)

    seen = [r.content for r in _items(db, unit_id, current_student.user_id)]
    try:
        data = await run_in_threadpool(I.lesson_items, unit.title, unit.theory or '',
                                       I.LESSON_BATCH, seen)
    except Exception as e:
        raise _ai_error(e)

    rows = _clean_items(data)
    if not rows:
        raise HTTPException(status_code=503,
                            detail="Could not create the practice set — please try again.")

    db.query(SpeakingPronItem).filter(
        SpeakingPronItem.unit_id == unit_id,
        SpeakingPronItem.user_id == current_student.user_id).delete(
            synchronize_session=False)
    for i, row in enumerate(rows):
        db.add(SpeakingPronItem(unit_id=unit_id, user_id=current_student.user_id,
                                order_index=i, **row))
    db.commit()

    items = _items(db, unit_id, current_student.user_id)
    return {"items": [_serialise(i) for i in items], "personalised": True,
            "quota": Q.quota(db, current_student)}


def _clean_items(data: dict) -> List[dict]:
    """Lọc bản AI trả về thành đúng những dòng ghi được xuống DB.

    Dùng chung với đường admin: hai bên sinh bằng cùng một prompt thì cũng phải chấp nhận
    cùng một tập dữ liệu, không thì bộ mặc định và bộ cá nhân lệch nhau.
    """
    out = []
    for raw in (data or {}).get('items') or []:
        if not isinstance(raw, dict):
            continue
        content = ' '.join(str(raw.get('content') or '').split())[:500]
        if not content:
            continue
        kind = 'sentence' if str(raw.get('kind')).lower().startswith('s') else 'word'
        note = ' '.join(str(raw.get('note') or '').split())[:500] or None
        out.append({"kind": kind, "content": content, "note": note})
    return out[:I.LESSON_BATCH]


@router.post("/speaking/lessons/items/{item_id}/score", response_model=dict)
async def score_item(item_id: int,
                     audio: UploadFile = File(...),
                     current_student: User = Depends(get_current_student),
                     db: Session = Depends(get_db)):
    """Học viên đọc một mục rồi AI chấm.

    Từ → chấm phát âm (âm + trọng âm). Câu → shadowing (nhịp, nối âm, ngữ điệu). Cùng hai
    bộ prompt của trang phân tích, chỉ khác chỗ gọi.
    """
    require_speaking_access(current_student)
    item = db.query(SpeakingPronItem).filter(
        SpeakingPronItem.item_id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Practice item not found.")
    # Bộ riêng của người khác thì không đụng vào được.
    if item.user_id is not None and item.user_id != current_student.user_id:
        raise HTTPException(status_code=404, detail="Practice item not found.")
    # Mục nằm trong bài bị khoá thì cũng không chấm: chặn ở màn mở bài là chưa đủ, id của
    # mục đoán được và đây mới là chỗ gọi AI.
    _require_unit_practisable(db, current_student, _unit(db, item.unit_id))

    raw, mime = await _read_audio(audio)
    target = item.content[:MAX_TARGET_CHARS]
    scorer = I.score_shadowing if item.kind == 'sentence' else I.score_pronunciation

    # Cửa kiểm "có người đọc đúng câu đó không" (lỗi 19/09: im lặng được 96 điểm). Im lặng
    # thì chưa trừ lượt; đã qua lượt nghe AI thì tính lượt — xem speaking_practice.py.
    def no_score(problem):
        return {"item_id": item.item_id, "kind": item.kind, "target_text": target,
                "score": None, "problem": problem, "quota": Q.quota(db, current_student)}
    silent = await run_in_threadpool(I.signal_problem, raw, _debug_tag(current_student, 'lesson', target))
    if silent:
        return no_score(silent)
    Q.take(db, current_student)
    wrong = await run_in_threadpool(I.speech_problem, target, raw, mime)
    if wrong:
        return no_score(wrong)
    try:
        data = await run_in_threadpool(scorer, target, raw, mime)
    except Exception as e:
        Q.give_back(db, current_student)
        raise _ai_error(e)

    score = data.get('score')
    try:
        score = max(0, min(100, int(round(float(score))))) if score is not None else None
    except (TypeError, ValueError):
        score = None

    out = {"item_id": item.item_id, "kind": item.kind, "target_text": target,
           "score": score, "problem": data.get('problem'),
           "quota": Q.quota(db, current_student)}
    if item.kind == 'sentence':
        out.update({
            "dimensions": [d for d in (data.get('dimensions') or [])
                           if isinstance(d, dict) and d.get('key') in I.SHADOW_DIMENSIONS],
            "summary": data.get('summary'),
            "scale_note": "This 0–100 score is for shadowing practice only — "
                          "it is not an IELTS Pronunciation band.",
        })
    else:
        out.update({
            "sounds": data.get('sounds') or [],
            "word_stress": data.get('word_stress') or [],
            "word_stress_verdict": data.get('word_stress_verdict'),
            "clarity": data.get('clarity'), "tips": data.get('tips') or [],
            "scale_note": "This 0–100 scale is for pronunciation practice only — "
                          "it is not an IELTS Pronunciation band.",
        })
    return out
