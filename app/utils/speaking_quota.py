"""Một túi lượt duy nhất cho mọi chức năng luyện có gọi AI của Speaking.

Feedback 14/09: "10 lượt luyện Pronunciation/Shadowing mỗi ngày đối với NO VIP ở tất cả
mặt trận. Tránh nó spam tốn tiền."

Trước đây mỗi chỗ tự đếm một kiểu và đếm lệch nhau:

    /speaking/practice/pronunciation   KHÔNG giới hạn  ← lỗ hổng: gọi Gemini audio vô hạn
    /speaking/practice/shadowing       1 lượt/ngày
    /speaking/lessons/items/{id}/score 10 lượt/ngày (bảng riêng 'pron_lesson')

Ba đường đó cùng một việc (học viên đọc, AI nghe rồi chấm) và cùng một túi tiền, nên giờ
gom về một khoá đếm duy nhất. Ai tiêu ở đâu cũng trừ chung, hết là hết ở mọi cửa.

VIP và tài khoản test: không đếm, không trừ (`remaining = None` nghĩa là vô hạn, KHÁC hẳn
0 là đã hết).
"""
from sqlalchemy.orm import Session

from app.models.models import SpeakingDailyUsage, User
from app.utils.datetime_utils import get_vietnam_time
from app.utils.speaking_gate import unlimited_for_testing

# Khoá đếm dùng chung. Tên cũ 'shadowing' và 'pron_lesson' bỏ lại trong DB, không xoá:
# dữ liệu cũ giữ nguyên còn lượt hôm nay thì đếm sang khoá mới.
PRACTICE_FEATURE = 'practice_ai'
PRACTICE_DAILY_LIMIT = 10

LIMIT_MESSAGE = (
    f"You have used all {PRACTICE_DAILY_LIMIT} AI practice turns for today "
    "(pronunciation practice, AI Shadowing and pronunciation lessons combined). "
    "Upgrade to VIP for unlimited practice."
)


def _today():
    return get_vietnam_time().date()


def is_unlimited(db: Session, user: User) -> bool:
    # Nạp muộn: speaking_test nạp models và cả module này, nhập ở đầu file là vòng lặp.
    from app.routes.student.speaking_test import is_vip
    return is_vip(user, db) or unlimited_for_testing(user)


def usage_row(db: Session, user_id: int) -> SpeakingDailyUsage:
    row = db.query(SpeakingDailyUsage).filter(
        SpeakingDailyUsage.user_id == user_id,
        SpeakingDailyUsage.day == _today(),
        SpeakingDailyUsage.feature == PRACTICE_FEATURE).first()
    if row is None:
        row = SpeakingDailyUsage(user_id=user_id, day=_today(),
                                 feature=PRACTICE_FEATURE, used=0)
        db.add(row)
        db.flush()
    return row


def quota(db: Session, user: User) -> dict:
    """`remaining = None` là vô hạn. Giao diện phải phân biệt được nó với 0."""
    if is_unlimited(db, user):
        return {"limit": None, "used": 0, "remaining": None}
    row = usage_row(db, user.user_id)
    return {"limit": PRACTICE_DAILY_LIMIT, "used": row.used,
            "remaining": max(0, PRACTICE_DAILY_LIMIT - row.used)}


def take(db: Session, user: User):
    """Trừ một lượt TRƯỚC khi gọi AI, và commit ngay.

    Gọi xong mới trừ thì bấm nhanh hai lần là lọt hai lượt, mà mỗi lượt là một lần trả
    tiền. Trả về dòng đếm để hoàn lại khi AI hỏng, hoặc None nếu tài khoản vô hạn.
    """
    if is_unlimited(db, user):
        return None
    row = usage_row(db, user.user_id)
    if row.used >= PRACTICE_DAILY_LIMIT:
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail=LIMIT_MESSAGE)
    row.used += 1
    db.commit()
    return row


def give_back(db: Session, user: User):
    """AI hỏng thì trả lại lượt: không tính tiền học viên cho thứ họ không nhận được."""
    if is_unlimited(db, user):
        return
    row = usage_row(db, user.user_id)
    row.used = max(0, row.used - 1)
    db.commit()
