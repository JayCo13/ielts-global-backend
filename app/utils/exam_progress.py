"""Read the tab-switch count of EXACTLY the attempt being submitted.

`exam_progress` keeps only ONE row per student (`user_id` is unique): it is a
"what are they doing right now" snapshot written by the exam-room heartbeat, not a
per-attempt history. The next exam's heartbeat overwrites the previous one.

Consequence: reading that row blindly at submit time could pick up the number of a
DIFFERENT exam — a student who did Writing (4 tab switches) and then submits
Listening would inherit the 4. For a number used to flag possible cheating, a wrong
attribution is far worse than leaving it blank, so we only accept the value when the
progress row really belongs to this exam.
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import ExamProgress


def tab_switches_for(db: Session, user_id: int, exam_id: Optional[int],
                     skill: Optional[str] = None) -> Optional[int]:
    """Tab switches of the attempt being submitted, or None if we can't be sure the
    row belongs to this attempt.

    None means "unknown" and the UI hides the line — different from 0, which means
    "measured, and the student never left the tab".
    """
    row = db.query(ExamProgress).filter(ExamProgress.user_id == user_id).first()
    if row is None:
        return None
    if exam_id is not None and row.exam_id is not None and row.exam_id != exam_id:
        return None                      # the row is about a different exam
    if skill and row.skill and row.skill != skill:
        return None
    return row.tab_switches
