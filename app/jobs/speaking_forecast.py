"""Daily forecast upkeep for Speaking topics.

Speaking decays on a different rule from the other skills (docs/speaking-spec.md §3).
Reading/listening/writing lose a point when nobody has touched a part for N days;
a Speaking topic instead lives or dies by the appearance window an admin typed:

    today inside  appear_from..appear_to  →  active, keeps its occurrence count
    today outside that window             →  occurrence forced to 0, hidden from forecast

`last_updated` deliberately plays no part — an admin editing a topic must not extend how
long it is predicted for. Part 3 needs no handling of its own: its questions belong to a
Part 2 topic, so it inherits that topic's state.

Stars are the same percentile split the other skills use, ranked per part so Part 1 and
Part 2 don't dilute each other.

    docker compose exec -T backend python -m app.jobs.speaking_forecast
"""
from sqlalchemy.orm import Session

from app.models.models import SpeakingTopic
from app.utils.datetime_utils import get_vietnam_time


def recompute_levels(db: Session, topics=None):
    """Top 15% → 4 stars, next 25% → 3, next 25% → 2, rest → 1. Ranked within each part."""
    if topics is None:
        topics = db.query(SpeakingTopic).all()

    by_part = {}
    for t in topics:
        if t.is_active and (t.occurrence_count or 0) >= 1:
            by_part.setdefault(t.part, []).append(t)
        else:
            t.forecast_level = None

    for part_topics in by_part.values():
        part_topics.sort(key=lambda t: -(t.occurrence_count or 0))
        n = len(part_topics)
        for i, t in enumerate(part_topics):
            q = i / n
            t.forecast_level = 4 if q < 0.15 else 3 if q < 0.40 else 2 if q < 0.65 else 1


def refresh_topic(db: Session, topic) -> bool:
    """Đồng bộ lại cờ Forecast của MỘT topic với cửa sổ xuất hiện vừa được sửa.

    Không phải chạy decay: decay là việc admin bấm nút, còn đây chỉ là giữ cho cờ khớp
    với dữ liệu vừa nhập. Sửa cửa sổ cho topic quay lại kỳ dự đoán mà nó vẫn nằm ngoài
    Forecast thì admin không hiểu vì sao (feedback 31/08).

    Nhắc lại cho khỏi nhầm: `is_active = False` chỉ có nghĩa "không hiện trong Forecast".
    Topic đó vẫn được dùng để tổ hợp đề bình thường.
    """
    if not topic.appear_from:
        return False
    today = get_vietnam_time().date()
    end = topic.appear_to or topic.appear_from
    inside = topic.appear_from <= today <= end
    changed = topic.is_active != inside
    topic.is_active = inside
    if inside:
        if (topic.occurrence_count or 0) < 1:
            topic.occurrence_count = 1
    else:
        topic.occurrence_count = 0
    return changed


def run_decay(db: Session) -> dict:
    today = get_vietnam_time().date()
    topics = db.query(SpeakingTopic).all()

    activated = deactivated = no_window = 0
    for t in topics:
        if not t.appear_from:
            # No window given: leave it alone rather than guessing it has expired.
            no_window += 1
            continue
        end = t.appear_to or t.appear_from
        inside = t.appear_from <= today <= end
        if inside:
            # Back inside the window: it counts again. The spec words this as
            # "Occurrence Times = 1", which is applied as a floor rather than an
            # overwrite — an admin who typed 5 keeps their 5, and only a topic that had
            # been zeroed comes back at 1. Overwriting would flatten every active topic
            # to the same count and leave every star rating identical.
            if (t.occurrence_count or 0) < 1:
                t.occurrence_count = 1
            if not t.is_active:
                t.is_active = True
                activated += 1
        elif t.is_active:
            t.is_active = False
            t.occurrence_count = 0
            deactivated += 1

    recompute_levels(db, topics)
    db.commit()
    return {'topics': len(topics), 'activated': activated,
            'deactivated': deactivated, 'no_window': no_window}


def main():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        print("Speaking forecast:", run_decay(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
