"""Single source of truth for what each VIP package type unlocks.

Ported from the Vietnam tree (ielts-main-nov). `package_type = 'all_skills'`
predates paid Writing, so "ALL SKILLS" only ever meant **Listening + Reading**.
Writing VIP comes only from a `single_skill` package with `skill_type = 'writing'`.

Import `package_covers()` instead of re-implementing the filter inline.
"""
from sqlalchemy import and_, or_

from app.models.models import VIPPackage

# The only skills the legacy "ALL SKILLS" bundle actually covers.
ALL_SKILLS_COVERS = ("listening", "reading")

# ExamSection.section_type → VIP skill_type. A Writing section is stored as 'essay',
# so comparing section_type directly against skill_type silently never matches.
SECTION_TO_SKILL = {"listening": "listening", "reading": "reading", "essay": "writing"}


def package_covers(skill: str):
    """SQLAlchemy filter selecting packages that unlock `skill`.

    `skill` is a VIP skill_type ('listening' | 'reading' | 'writing') — map an
    ExamSection.section_type through SECTION_TO_SKILL first.
    """
    single = and_(VIPPackage.package_type == "single_skill", VIPPackage.skill_type == skill)
    if skill in ALL_SKILLS_COVERS:
        return or_(VIPPackage.package_type == "all_skills", single)
    return single
