import os
import re
import json
import html as html_lib
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Exam, ExamSection, WritingTask, SpeakingMaterial
from app.enums.enums import TASK1_QUESTION_TYPE_ORDER, TASK2_QUESTION_TYPE_ORDER
from typing import List

router = APIRouter()

@router.get("/listening-tests", response_model=List[dict])
async def get_public_listening_tests(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'listening'
    ).distinct().order_by(Exam.exam_id).all()

    exam_ids = [e.exam_id for e in exams]
    if not exam_ids:
        return []

    all_sections = db.query(ExamSection).filter(
        ExamSection.exam_id.in_(exam_ids),
        ExamSection.section_type == 'listening'
    ).order_by(ExamSection.order_number).all()
    
    sections_by_exam = {}
    for s in all_sections:
        sections_by_exam.setdefault(s.exam_id, []).append(s)

    result = []
    for exam in exams:
        sections = sections_by_exam.get(exam.exam_id, [])
        if not sections:
            continue
        first_section = sections[0]
        part_titles = {s.order_number: s.part_title for s in sections if s.part_title}
        
        result.append({
            "exam_id": exam.exam_id,
            "title": exam.title,
            "created_at": exam.created_at,
            "duration": first_section.duration,
            "total_marks": first_section.total_marks,
            "is_completed": False,
            "total_score": 0,
            "part_titles": part_titles
        })
    return result

@router.get("/reading-tests", response_model=List[dict])
async def get_public_reading_tests(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'reading'
    ).distinct().order_by(Exam.exam_id).all()

    exam_ids = [e.exam_id for e in exams]
    if not exam_ids:
        return []

    all_sections = db.query(ExamSection).filter(
        ExamSection.exam_id.in_(exam_ids),
        ExamSection.section_type == 'reading'
    ).order_by(ExamSection.order_number).all()
    
    sections_by_exam = {}
    for s in all_sections:
        sections_by_exam.setdefault(s.exam_id, []).append(s)

    result = []
    for exam in exams:
        sections = sections_by_exam.get(exam.exam_id, [])
        if not sections:
            continue
        first_section = sections[0]
        part_titles = {s.order_number: s.part_title for s in sections if s.part_title}
        
        result.append({
            "exam_id": exam.exam_id,
            "title": exam.title,
            "created_at": exam.created_at,
            "duration": first_section.duration,
            "total_marks": first_section.total_marks,
            "is_completed": False,
            "total_score": 0,
            "part_titles": part_titles
        })
    return result

@router.get("/writing-forecasts", response_model=List[dict])
async def get_public_writing_forecasts(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'essay'
    ).distinct().order_by(Exam.exam_id).all()

    exam_ids = [e.exam_id for e in exams]
    if not exam_ids:
        return []

    all_forecast_tasks = db.query(WritingTask).filter(
        WritingTask.test_id.in_(exam_ids),
        WritingTask.is_forecast == True
    ).order_by(WritingTask.part_number).all()
    
    tasks_by_exam = {}
    for t in all_forecast_tasks:
        tasks_by_exam.setdefault(t.test_id, []).append(t)

    result = []
    for exam in exams:
        forecast_tasks = tasks_by_exam.get(exam.exam_id, [])
        if not forecast_tasks:
            continue
        part1_task1_type = next(
            (t.task1_type for t in forecast_tasks if t.part_number == 1 and t.task1_type),
            None,
        )
        part2_task2_type = next(
            (t.task2_type for t in forecast_tasks if t.part_number == 2 and t.task2_type),
            None,
        )
        result.append({
            "exam_id": exam.exam_id,
            "exam_title": exam.title,
            "task1_type": part1_task1_type,
            "task2_type": part2_task2_type,
            "parts": [{
                "task_id": t.task_id,
                "part_number": t.part_number,
                "title": t.title,
                "task_type": t.task_type,
                "task1_type": t.task1_type,
                "task2_type": t.task2_type,
                "instructions": "",
                "word_limit": t.word_limit,
                "is_recommended": bool(getattr(t, 'is_recommended', False))
            } for t in forecast_tasks]
        })

    # Primary sort: Task 1 question type in fixed order (pie, map, process,
    # table, line, bar, mixed); rows without a type sort last.
    type_order = {t: i for i, t in enumerate(TASK1_QUESTION_TYPE_ORDER)}
    result.sort(key=lambda r: type_order.get(r["task1_type"], len(type_order)))

    return result

@router.get("/listening-forecasts", response_model=List[dict])
async def get_public_listening_forecasts(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'listening'
    ).distinct().order_by(Exam.exam_id).all()

    result = []
    for exam in exams:
        sections = db.query(ExamSection).filter(
            ExamSection.exam_id == exam.exam_id,
            ExamSection.section_type == 'listening'
        ).order_by(ExamSection.order_number).all()

        forecast_sections = [s for s in sections if getattr(s, 'is_forecast', False)]
        if not forecast_sections:
            continue

        forecast_parts = []
        for s in forecast_sections:
            forecast_parts.append({
                "part_number": s.order_number,
                "forecast_title": getattr(s, 'forecast_title', None),
                "completed": False,
                "attempts_count": 0,
                "is_recommended": bool(getattr(s, 'is_recommended', False)),
                "question_types": getattr(s, 'question_types', None) or []
            })

        result.append({
            "exam_id": exam.exam_id,
            "exam_title": exam.title,
            "parts": forecast_parts
        })
    return result

@router.get("/reading-forecasts", response_model=List[dict])
async def get_public_reading_forecasts(db: Session = Depends(get_db)):
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == 'reading'
    ).distinct().order_by(Exam.exam_id).all()

    result = []
    for exam in exams:
        sections = db.query(ExamSection).filter(
            ExamSection.exam_id == exam.exam_id,
            ExamSection.section_type == 'reading'
        ).order_by(ExamSection.order_number).all()

        forecast_sections = [s for s in sections if getattr(s, 'is_forecast', False)]
        if not forecast_sections:
            continue

        forecast_parts = []
        for s in forecast_sections:
            forecast_parts.append({
                "part_number": s.order_number,
                "forecast_title": getattr(s, 'forecast_title', None),
                "completed": False,
                "attempts_count": 0,
                "is_recommended": bool(getattr(s, 'is_recommended', False)),
                "question_types": getattr(s, 'question_types', None) or []
            })

        result.append({
            "exam_id": exam.exam_id,
            "exam_title": exam.title,
            "parts": forecast_parts
        })
    return result

@router.get("/speaking/materials", response_model=List[dict])
async def get_public_speaking_materials(part: str = None, db: Session = Depends(get_db)):
    query = db.query(SpeakingMaterial)
    if part:
        query = query.filter(SpeakingMaterial.part_type == part)
    materials = query.order_by(SpeakingMaterial.created_at.desc()).all()
    
    results = []
    for m in materials:
        results.append({
            "material_id": m.material_id,
            "title": m.title,
            "part_type": m.part_type,
            "pdf_url": m.pdf_url,
            "created_at": m.created_at,
            "has_access": False
        })
    return results


# ---------------------------------------------------------------------------
# SEO landing pages + dynamic sitemap
#
# These are purely additive, read-only public endpoints. They render a small,
# crawlable HTML page per full test that exposes ONLY the public part titles
# (ExamSection.part_title) so search engines can index queries like a specific
# part name. The passage / questions / answers and forecast-specific titles are
# never included — those stay gated behind the app login as before. Because the
# data is read live from the DB, a newly created admin test is crawlable the
# moment it is active, with no rebuild/redeploy.
# ---------------------------------------------------------------------------

SEO_SKILL_SECTION = {"listening": "listening", "reading": "reading", "writing": "essay"}
SEO_APP_URL = os.getenv("PUBLIC_APP_URL", "https://ieltscomputertest.com").rstrip("/")


def _seo_clean(text):
    """Strip any HTML tags and collapse whitespace for safe display."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _seo_slugify(text):
    text = re.sub(r"<[^>]+>", " ", text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-") or "test"


def _seo_active_exams(db, section_type):
    """Active exams for a skill that have at least one public part_title, with
    their ordered (part_number, clean part_title) pairs."""
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == section_type
    ).distinct().order_by(Exam.exam_id.desc()).all()
    if not exams:
        return []
    exam_ids = [e.exam_id for e in exams]
    sections = db.query(ExamSection).filter(
        ExamSection.exam_id.in_(exam_ids),
        ExamSection.section_type == section_type
    ).order_by(ExamSection.order_number).all()
    by_exam = {}
    for s in sections:
        clean = _seo_clean(s.part_title)
        if clean:
            by_exam.setdefault(s.exam_id, []).append((s.order_number, clean))
    return [(e, by_exam[e.exam_id]) for e in exams if e.exam_id in by_exam]


def _seo_active_sections(db, section_type):
    """Each active section (part) for a skill that has a public part_title, as
    (section, exam_title, exam_created_at). One crawlable page is generated per
    part so a specific part name (e.g. "Music alive agency") gets its own URL."""
    rows = db.query(ExamSection, Exam.title, Exam.created_at).join(
        Exam, Exam.exam_id == ExamSection.exam_id
    ).filter(
        Exam.is_active == True,
        ExamSection.section_type == section_type
    ).order_by(Exam.exam_id.desc(), ExamSection.order_number).all()
    return [(sec, title, created) for sec, title, created in rows if _seo_clean(sec.part_title)]


def _seo_not_found():
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"robots\" content=\"noindex\"><title>Not found</title></head>"
        f"<body><h1>Test not found</h1><p><a href=\"{SEO_APP_URL}\">Back to ieltscomputertest.com</a></p></body></html>"
    )


@router.get("/sitemap-exams.xml", include_in_schema=False)
async def sitemap_exams(request: Request, db: Session = Depends(get_db)):
    base = str(request.base_url).rstrip("/")
    rows = []
    for skill, section_type in SEO_SKILL_SECTION.items():
        # Per-exam pages (hub: all parts of a test)
        for exam, _parts in _seo_active_exams(db, section_type):
            loc = f"{base}/public/t/{skill}/{exam.exam_id}/{_seo_slugify(exam.title)}"
            lastmod = exam.created_at.date().isoformat() if exam.created_at else None
            rows.append((loc, lastmod))
        # Per-part pages (one URL per part, slug = part title)
        for sec, _exam_title, created in _seo_active_sections(db, section_type):
            loc = f"{base}/public/p/{skill}/{sec.section_id}/{_seo_slugify(sec.part_title)}"
            lastmod = created.date().isoformat() if created else None
            rows.append((loc, lastmod))
    items = []
    for loc, lastmod in rows:
        lm = f"\n    <lastmod>{lastmod}</lastmod>" if lastmod else ""
        items.append(
            f"  <url>\n    <loc>{html_lib.escape(loc)}</loc>{lm}\n"
            f"    <changefreq>weekly</changefreq>\n    <priority>0.7</priority>\n  </url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(items)
        + "\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/t/{skill}/{exam_id}", response_class=HTMLResponse, include_in_schema=False)
@router.get("/t/{skill}/{exam_id}/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def seo_test_page(skill: str, exam_id: int, request: Request, slug: str = None,
                        db: Session = Depends(get_db)):
    section_type = SEO_SKILL_SECTION.get(skill)
    if not section_type:
        return HTMLResponse(_seo_not_found(), status_code=404)
    exam = db.query(Exam).filter(Exam.exam_id == exam_id, Exam.is_active == True).first()
    if not exam:
        return HTMLResponse(_seo_not_found(), status_code=404)
    sections = db.query(ExamSection).filter(
        ExamSection.exam_id == exam_id,
        ExamSection.section_type == section_type
    ).order_by(ExamSection.order_number).all()
    parts = [(s.order_number, _seo_clean(s.part_title)) for s in sections
             if _seo_clean(s.part_title)]
    if not parts:
        return HTMLResponse(_seo_not_found(), status_code=404)

    skill_label = skill.capitalize()
    exam_title = _seo_clean(exam.title) or f"IELTS {skill_label} Test"
    base = str(request.base_url).rstrip("/")
    canonical = f"{base}/public/t/{skill}/{exam_id}/{_seo_slugify(exam.title)}"
    part_titles = [t for _n, t in parts]
    title_tag = f"{exam_title} — IELTS {skill_label} Computer Test | ieltscomputertest.com"
    description = (
        f"{exam_title}: practice IELTS {skill_label} on the computer test. "
        f"Parts: {', '.join(part_titles)}. Free practice on a 100% real exam "
        f"interface at ieltscomputertest.com."
    )[:300]

    others = []
    for e, _p in _seo_active_exams(db, section_type):
        if e.exam_id != exam_id:
            others.append((e, _seo_slugify(e.title)))
        if len(others) >= 12:
            break

    parts_html = "\n".join(
        f'      <li><span class="part">Part {n}</span> {html_lib.escape(t)}</li>'
        for n, t in parts
    )
    others_html = "\n".join(
        f'      <li><a href="{base}/public/t/{skill}/{e.exam_id}/{s}">'
        f'{html_lib.escape(_seo_clean(e.title))}</a></li>'
        for e, s in others
    )
    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "LearningResource",
        "name": exam_title,
        "url": canonical,
        "learningResourceType": "IELTS practice test",
        "educationalLevel": "IELTS",
        "inLanguage": "en",
        "about": f"IELTS {skill_label}",
        "teaches": part_titles,
        "isAccessibleForFree": True,
        "provider": {
            "@type": "EducationalOrganization",
            "name": "ieltscomputertest.com",
            "url": SEO_APP_URL,
        },
    }, ensure_ascii=False)

    app_link = f"{SEO_APP_URL}/{skill}_list"
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <title>{html_lib.escape(title_tag)}</title>
  <meta name="description" content="{html_lib.escape(description)}">
  <link rel="canonical" href="{html_lib.escape(canonical)}">
  <meta property="og:type" content="article">
  <meta property="og:title" content="{html_lib.escape(title_tag)}">
  <meta property="og:description" content="{html_lib.escape(description)}">
  <meta property="og:url" content="{html_lib.escape(canonical)}">
  <meta property="og:site_name" content="ieltscomputertest.com">
  <script type="application/ld+json">{json_ld}</script>
  <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#0e233a;max-width:760px;margin:0 auto;padding:24px;line-height:1.6}}
    a{{color:#0096b1}}
    .crumb{{font-size:14px;color:#6b7280;margin-bottom:8px}}
    h1{{font-size:28px;margin:.2em 0}}
    .part{{display:inline-block;background:#e8f7ed;color:#1f7a44;font-weight:600;font-size:12px;padding:2px 8px;border-radius:999px;margin-right:8px}}
    ul{{list-style:none;padding:0}}
    li{{padding:10px 0;border-bottom:1px solid #eef1f4}}
    .cta{{display:inline-block;margin:20px 0;background:linear-gradient(90deg,#c98825,#e4b231);color:#fff;font-weight:700;padding:14px 28px;border-radius:12px;text-decoration:none}}
    .more a{{display:block}}
  </style>
</head>
<body>
  <nav class="crumb"><a href="{SEO_APP_URL}">Home</a> / <a href="{app_link}">IELTS {skill_label}</a> / {html_lib.escape(exam_title)}</nav>
  <h1>{html_lib.escape(exam_title)} — IELTS {skill_label} Computer Test</h1>
  <p>Practice <strong>{html_lib.escape(exam_title)}</strong> for the IELTS {skill_label} section on a 100% real computer-based exam interface. This test includes the following parts:</p>
  <ul>
{parts_html}
  </ul>
  <a class="cta" href="{app_link}">Start practicing on ieltscomputertest.com →</a>
  <h2>More IELTS {skill_label} tests</h2>
  <ul class="more">
{others_html}
  </ul>
</body>
</html>
"""
    return HTMLResponse(content=page)


@router.get("/p/{skill}/{section_id}", response_class=HTMLResponse, include_in_schema=False)
@router.get("/p/{skill}/{section_id}/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def seo_part_page(skill: str, section_id: int, request: Request, slug: str = None,
                        db: Session = Depends(get_db)):
    section_type = SEO_SKILL_SECTION.get(skill)
    if not section_type:
        return HTMLResponse(_seo_not_found(), status_code=404)
    sec = db.query(ExamSection).filter(
        ExamSection.section_id == section_id,
        ExamSection.section_type == section_type
    ).first()
    if not sec or not _seo_clean(sec.part_title):
        return HTMLResponse(_seo_not_found(), status_code=404)
    exam = db.query(Exam).filter(Exam.exam_id == sec.exam_id, Exam.is_active == True).first()
    if not exam:
        return HTMLResponse(_seo_not_found(), status_code=404)

    skill_label = skill.capitalize()
    part_title = _seo_clean(sec.part_title)
    exam_title = _seo_clean(exam.title) or f"IELTS {skill_label} Test"
    base = str(request.base_url).rstrip("/")
    canonical = f"{base}/public/p/{skill}/{section_id}/{_seo_slugify(part_title)}"
    qtypes = [q for q in (sec.question_types or []) if isinstance(q, str)]
    title_tag = f"{part_title} — IELTS {skill_label} Practice | ieltscomputertest.com"
    description = (
        f"Practice \"{part_title}\" — Part {sec.order_number} of {exam_title}, "
        f"IELTS {skill_label} computer test. "
        + (f"Question types: {', '.join(qtypes)}. " if qtypes else "")
        + "Free practice on a 100% real exam interface at ieltscomputertest.com."
    )[:300]

    # sibling parts of the same exam (internal links)
    siblings = db.query(ExamSection).filter(
        ExamSection.exam_id == sec.exam_id,
        ExamSection.section_type == section_type
    ).order_by(ExamSection.order_number).all()
    siblings_html = "\n".join(
        f'      <li><a href="{base}/public/p/{skill}/{s.section_id}/{_seo_slugify(s.part_title)}">'
        f'Part {s.order_number} — {html_lib.escape(_seo_clean(s.part_title))}</a></li>'
        for s in siblings if _seo_clean(s.part_title) and s.section_id != section_id
    )
    qtypes_html = (
        "<p>Question types: " + ", ".join(html_lib.escape(q) for q in qtypes) + ".</p>"
        if qtypes else ""
    )
    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "LearningResource",
        "name": part_title,
        "url": canonical,
        "learningResourceType": "IELTS practice test part",
        "educationalLevel": "IELTS",
        "inLanguage": "en",
        "about": f"IELTS {skill_label}",
        "isPartOf": exam_title,
        "isAccessibleForFree": True,
        "provider": {
            "@type": "EducationalOrganization",
            "name": "ieltscomputertest.com",
            "url": SEO_APP_URL,
        },
    }, ensure_ascii=False)
    app_link = f"{SEO_APP_URL}/{skill}_list"
    exam_link = f"{base}/public/t/{skill}/{exam.exam_id}/{_seo_slugify(exam.title)}"
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="index, follow, max-image-preview:large">
  <title>{html_lib.escape(title_tag)}</title>
  <meta name="description" content="{html_lib.escape(description)}">
  <link rel="canonical" href="{html_lib.escape(canonical)}">
  <meta property="og:type" content="article">
  <meta property="og:title" content="{html_lib.escape(title_tag)}">
  <meta property="og:description" content="{html_lib.escape(description)}">
  <meta property="og:url" content="{html_lib.escape(canonical)}">
  <meta property="og:site_name" content="ieltscomputertest.com">
  <script type="application/ld+json">{json_ld}</script>
  <style>
    body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#0e233a;max-width:760px;margin:0 auto;padding:24px;line-height:1.6}}
    a{{color:#0096b1}}
    .crumb{{font-size:14px;color:#6b7280;margin-bottom:8px}}
    h1{{font-size:28px;margin:.2em 0}}
    .cta{{display:inline-block;margin:20px 0;background:linear-gradient(90deg,#c98825,#e4b231);color:#fff;font-weight:700;padding:14px 28px;border-radius:12px;text-decoration:none}}
    ul{{list-style:none;padding:0}} li{{padding:8px 0;border-bottom:1px solid #eef1f4}}
  </style>
</head>
<body>
  <nav class="crumb"><a href="{SEO_APP_URL}">Home</a> / <a href="{app_link}">IELTS {skill_label}</a> / <a href="{exam_link}">{html_lib.escape(exam_title)}</a> / Part {sec.order_number}</nav>
  <h1>{html_lib.escape(part_title)}</h1>
  <p><strong>{html_lib.escape(part_title)}</strong> is Part {sec.order_number} of <a href="{exam_link}">{html_lib.escape(exam_title)}</a> — IELTS {skill_label} on the computer-based test. Practice it on a 100% real exam interface.</p>
  {qtypes_html}
  <a class="cta" href="{app_link}">Start practicing on ieltscomputertest.com →</a>
  <h2>Other parts of {html_lib.escape(exam_title)}</h2>
  <ul>
{siblings_html}
  </ul>
</body>
</html>
"""
    return HTMLResponse(content=page)
