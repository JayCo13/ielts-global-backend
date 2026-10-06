"""Cửa vào tính năng Speaking — ĐÃ MỞ CHO TẤT CẢ (21/09).

Global port: same behaviour as VN — Speaking is open to every account; SPEAKING_TESTERS only
grants unlimited quota + free Speaking VIP for testing. The maintenance message is English.

Lịch sử: khi Speaking còn đang viết dở, chỉ ba email test vào được, còn lại thấy panel
"đang cập nhật"; 20/09 mở thêm cho học viên trung tâm; 21/09 mở cho mọi tài khoản.

Danh sách `SPEAKING_TESTERS` ở dưới GIỜ CHỈ CÒN MỘT TÁC DỤNG: miễn mọi hạn mức và cho VIP
Speaking miễn phí (`unlimited_for_testing`). Nó không còn liên quan tới việc ai vào được.

Delete this module together with its call sites when Speaking ships:
  - app/routes/student/student_actions.py  (/speaking/access + the two materials routes)
  - ielts-tajun/src/components/Speaking_Fe.js
  - ielts-tajun/src/exam_elements/speaking/speaking_layout.js
"""
from fastapi import HTTPException, status

# Compared lower-cased; add a tester by adding a line here.
SPEAKING_TESTERS = {
    'letietan12.ielts@gmail.com',
    'taicopgm@gmail.com',
    'cotrinhhientai@gmail.com',
}

MAINTENANCE_MESSAGE = (
    "Speaking is being updated. Please check back later."
)


def speaking_is_open(user) -> bool:
    """MỞ CHO TẤT CẢ tài khoản (chủ dự án chốt 21/09) — Speaking đã ra mắt.

    Trước đó chỉ ba email test vào được, rồi thêm học viên trung tâm (20/09). Giữ hàm và
    các chỗ gọi `require_speaking_access` thay vì gỡ sạch: nếu cần đóng lại (sự cố, chi phí
    AI tăng đột biến) thì chỉ phải sửa đúng hàm này, không phải lần lại hơn hai chục chỗ.
    Đóng lại bằng cách trả về điều kiện cũ:
        return (getattr(user, 'email', '') or '').strip().lower() in SPEAKING_TESTERS

    "Mở cửa" KHÔNG phải "mở hạn mức": ai vào cũng theo luật của tài khoản mình —
    tài khoản thường 1 lượt thi + 1 lượt chấm mỗi ngày, chỉ điểm tổng quan, không thi
    nguyên đề, 10 lượt luyện phát âm/ngày; VIP Speaking phải mua gói riêng
    (`speaking_test.is_vip`).
    """
    return True


def unlimited_for_testing(user) -> bool:
    """Tài khoản trong DANH SÁCH EMAIL test được bỏ qua MỌI hạn mức của Speaking.

    Vì sao cần: hạn mức thật là 1 lượt thi và 1 lượt chấm mỗi ngày. Người test mà đụng
    trần sau bài đầu tiên thì không kiểm được luồng nào cho ra hồn, và mỗi lần lại phải
    chờ sang ngày hôm sau.

    CHỈ tính theo email, KHÔNG dùng `speaking_is_open`: từ 20/09 cửa vào còn mở cho học
    viên trung tâm, mà "được vào" với "không bị tính lượt" là hai chuyện khác nhau — gộp
    lại thì mọi học viên trung tâm nghiễm nhiên thành tài khoản test.

    GỠ CÙNG LÚC VỚI SPEAKING_TESTERS khi Speaking mở cho tất cả — nếu quên, ba tài khoản
    này sẽ vĩnh viễn không bị tính lượt.
    """
    return (getattr(user, 'email', '') or '').strip().lower() in SPEAKING_TESTERS


def require_speaking_access(user):
    """503 rather than 403: the feature is temporarily unavailable, not forbidden —
    and it lets the frontend tell "under maintenance" apart from a real permission
    error without matching on message text."""
    if not speaking_is_open(user):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=MAINTENANCE_MESSAGE,
        )
