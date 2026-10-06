"""Auto-forecast by Occurrence Count.

A "part" here = a full-test part: ExamSection (reading/listening, is_forecast False)
or WritingTask (is_forecast False). Each has occurrence_count; parts with
occurrence_count >= 1 are "in forecast". forecast_level (1-4) is a percentile of
occurrence_count computed SEPARATELY PER SKILL (reading / listening / writing) —
each skill's parts are ranked among themselves, not pooled together, so a skill
with few high-occurrence parts isn't diluted by another skill's distribution:
    Top 15% → 4 (Very likely), next 25% → 3 (High), next 25% → 2 (Medium),
    last 35% → 1 (Low).

Decay: if a part goes DECAY_DAYS with no change to occurrence_count, it drops by 1
(daily cron). skip_first_decay protects a freshly-discovered part (0→1) from the
first decay tick.

Run decay: POST /admin/forecast-auto/run-decay (global has no cron)
           or python -m app.jobs.forecast_auto
"""
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.models import ExamSection, WritingTask, SystemSetting
from app.utils.datetime_utils import get_vietnam_time

DECAY_DAYS_DEFAULT = 21
DECAY_DAYS_KEY = 'forecast_decay_days'


def get_decay_days(db: Session) -> int:
    row = db.query(SystemSetting).filter(SystemSetting.setting_key == DECAY_DAYS_KEY).first()
    if row and row.setting_value:
        try:
            return max(1, int(row.setting_value))
        except (ValueError, TypeError):
            pass
    return DECAY_DAYS_DEFAULT


def set_decay_days(db: Session, days: int):
    days = max(1, int(days))
    row = db.query(SystemSetting).filter(SystemSetting.setting_key == DECAY_DAYS_KEY).first()
    now = get_vietnam_time().replace(tzinfo=None)
    if row:
        row.setting_value = str(days)
        row.updated_at = now
    else:
        db.add(SystemSetting(setting_key=DECAY_DAYS_KEY, setting_value=str(days), updated_at=now))
    db.commit()
    return days


def _all_parts(db: Session):
    """All reading/listening sections + writing tasks — occurrence lives on each."""
    secs = (
        db.query(ExamSection)
        .filter(ExamSection.section_type.in_(['reading', 'listening']))
        .all()
    )
    tasks = db.query(WritingTask).all()
    return list(secs) + list(tasks)


def _skill_of(part) -> str:
    """'reading' / 'listening' for an ExamSection, else 'writing' (WritingTask)."""
    st = getattr(part, 'section_type', None)
    return st if st in ('reading', 'listening') else 'writing'


def recompute_levels(db: Session, parts=None):
    """Percentile-classify forecast_level over parts with occurrence_count >= 1,
    ranking each skill (reading/listening/writing) independently."""
    if parts is None:
        parts = _all_parts(db)
    # Clear parts that are out of forecast.
    for p in parts:
        if (p.occurrence_count or 0) < 1:
            p.forecast_level = None
    # Group active parts by skill, then percentile within each skill separately.
    by_skill = {}
    for p in parts:
        if (p.occurrence_count or 0) >= 1:
            by_skill.setdefault(_skill_of(p), []).append(p)
    for skill_parts in by_skill.values():
        skill_parts.sort(key=lambda p: -(p.occurrence_count or 0))
        n = len(skill_parts)
        for i, p in enumerate(skill_parts):
            q = i / n
            p.forecast_level = 4 if q < 0.15 else 3 if q < 0.40 else 2 if q < 0.65 else 1


def apply_occurrence_change(db: Session, part, new_count: int):
    """Set a part's occurrence_count, update last_updated + skip_first_decay, then
    recompute levels. Returns the part."""
    new_count = max(0, int(new_count))
    old = part.occurrence_count or 0
    if new_count == old:
        return part
    now = get_vietnam_time().replace(tzinfo=None)
    # Protect a freshly-discovered part (0 → ≥1) from the first decay tick.
    if old == 0 and new_count >= 1:
        part.skip_first_decay = True
    part.occurrence_count = new_count
    part.forecast_last_updated = now
    recompute_levels(db)
    db.commit()
    return part


def run_decay(db: Session) -> dict:
    """Daily: decay parts unchanged for >= decay_days. Returns a small summary."""
    decay_days = get_decay_days(db)
    now = get_vietnam_time().replace(tzinfo=None)
    parts = _all_parts(db)
    decayed = 0
    protected = 0
    for p in parts:
        if (p.occurrence_count or 0) < 1:
            continue
        last = p.forecast_last_updated
        if last is None:
            p.forecast_last_updated = now  # seed; decay starts counting from now
            continue
        if (now - last).days < decay_days:
            continue
        if (p.occurrence_count or 0) == 1 and p.skip_first_decay:
            # First protection: skip this decay, disarm the shield, reset the clock.
            p.skip_first_decay = False
            p.forecast_last_updated = now
            protected += 1
        else:
            p.occurrence_count = (p.occurrence_count or 0) - 1
            p.forecast_last_updated = now
            decayed += 1
    recompute_levels(db, parts)
    db.commit()
    return {"decayed": decayed, "protected": protected, "decay_days": decay_days}


def main():
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        print("Forecast decay:", run_decay(db))
    finally:
        db.close()


if __name__ == "__main__":
    main()
