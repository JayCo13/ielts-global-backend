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
SEO_APP_URL = os.getenv("PUBLIC_APP_URL", "https://englishoncomputer.com").rstrip("/")


def _seo_base(request):
    """Public base URL for canonical/sitemap/internal links.

    If PUBLIC_SEO_BASE_URL is set (e.g. "https://englishoncomputer.com"), use it
    verbatim — needed when these pages are reverse-proxied onto the main domain
    (Netlify/Cloudflare) so the URLs point at the brand domain, not the internal
    Koyeb host. Otherwise derive from the request, honouring X-Forwarded-Proto so
    URLs come out https on Koyeb (which terminates TLS and forwards HTTP)."""
    configured = os.getenv("PUBLIC_SEO_BASE_URL")
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host")
            or request.url.netloc)
    return f"{proto}://{host}".rstrip("/")


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
        f"<body><h1>Test not found</h1><p><a href=\"{SEO_APP_URL}\">Back to englishoncomputer.com</a></p></body></html>"
    )


_SEO_FREE_LIMIT = 6  # mirror testsPerPage / first-6 rule in Reading_Fe/Listening_Fe


def _seo_sort_key(title):
    """Replicate the student list's default alphabetical 'natural sort'
    (Reading_Fe/Listening_Fe): split the title on digit runs and zero-pad each
    number to 10 chars so "Test 9" sorts before "Test 10"."""
    out = []
    for item in re.split(r'([0-9]+)', title or ''):
        out.append(item.rjust(10, '0') if (item == '' or item.isdigit()) else item)
    return ''.join(out)


def _seo_free_exam_ids(db, section_type):
    """Exam ids a guest may open for free = the first N active exams for the
    skill in the app's default alphabetical order. This mirrors the frontend's
    "first 6 tests are free" rule (the gate guests actually experience)."""
    exams = db.query(Exam).join(ExamSection).filter(
        Exam.is_active == True,
        ExamSection.section_type == section_type
    ).distinct().all()
    exams_sorted = sorted(exams, key=lambda e: _seo_sort_key(e.title or ''))
    return {e.exam_id for e in exams_sorted[:_SEO_FREE_LIMIT]}


def _seo_exam_is_free(db, exam_id, section_type):
    """An exam is "free" iff it is among the first 6 in alphabetical order — the
    same set a guest can open in the app (Reading_Fe/Listening_Fe), not the
    ExamAccessType signal."""
    return exam_id in _seo_free_exam_ids(db, section_type)


# Shared stylesheet for the public SEO landing pages. Plain (non f-string) so the
# CSS braces don't need escaping; injected via {_SEO_CSS} into each page.
_SEO_CSS = """
    :root{--navy:#0b1f38;--navy2:#123c63;--teal:#0096b1;--teal2:#34c6da;--gold1:#d39a2e;--gold2:#f3c54e;--ink:#1f2d3d;--muted:#6b7a8d;--line:#e6ecf3;--bg:#eef3f8}
    *{box-sizing:border-box}
    html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
    body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--ink);line-height:1.6;background:var(--bg)}
    a{color:var(--teal);text-decoration:none}a:hover{text-decoration:underline}
    .hero{position:relative;overflow:hidden;color:#fff;background:radial-gradient(820px 460px at 88% -22%,rgba(52,198,218,.36),transparent 60%),radial-gradient(680px 460px at -8% 116%,rgba(243,197,78,.16),transparent 56%),linear-gradient(158deg,#0b1f38 0%,#123c63 60%,#0d2c4c 100%)}
    .nav{max-width:1060px;margin:0 auto;padding:18px 24px;display:flex;align-items:center;justify-content:space-between;gap:12px;position:relative;z-index:2}
    .brand{display:flex;align-items:center;gap:12px}
    .brand:hover{text-decoration:none}
    .brand img{height:62px;width:auto;display:block;filter:drop-shadow(0 6px 14px rgba(0,0,0,.3))}
    .brand-tx{font-weight:800;font-size:17px;color:#fff;letter-spacing:-.01em}
    .brand-tx b{color:#fff;font-weight:800}
    .nav-cta{font-weight:700;font-size:14px;color:#fff;border:1px solid rgba(255,255,255,.26);padding:10px 18px;border-radius:11px;background:rgba(255,255,255,.08);white-space:nowrap}
    .nav-cta:hover{text-decoration:none;background:rgba(255,255,255,.16);border-color:rgba(255,255,255,.5)}
    .hero-in{max-width:1060px;margin:0 auto;padding:30px 24px 96px;position:relative;z-index:2;text-align:center}
    .crumb{font-size:13px;color:rgba(255,255,255,.58);margin:4px 0 22px}
    .crumb a{color:rgba(255,255,255,.8)}.crumb a:hover{color:#fff}
    .badge{display:inline-flex;align-items:center;gap:9px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.2);color:#d9f4fb;font-weight:700;font-size:12px;letter-spacing:.06em;padding:8px 15px;border-radius:999px;margin-bottom:20px;text-transform:uppercase}
    .badge::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--teal2);box-shadow:0 0 0 4px rgba(52,198,218,.22)}
    h1{font-size:clamp(30px,5.6vw,54px);line-height:1.06;letter-spacing:-.025em;margin:0 auto 18px;max-width:20ch;font-weight:800}
    .lead{font-size:clamp(16px,2vw,20px);color:rgba(255,255,255,.84);margin:0 auto 26px;max-width:58ch}
    .lead strong{color:#fff}.lead a{color:#9fe6f3}
    .chips{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 32px;justify-content:center}
    .chip{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.09);border:1px solid rgba(255,255,255,.17);color:#eaf7fa;font-weight:600;font-size:13px;padding:9px 15px;border-radius:11px}
    .chip::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--gold2)}
    .cta-row{display:flex;flex-direction:column;align-items:center;gap:16px}
    .cta{display:inline-flex;align-items:center;gap:10px;background:linear-gradient(92deg,var(--gold1),var(--gold2));color:#241803;font-weight:800;font-size:17px;padding:18px 36px;border-radius:14px;border:0;cursor:pointer;box-shadow:0 16px 36px rgba(243,197,78,.34);transition:transform .14s,box-shadow .14s}
    .cta:hover{text-decoration:none;transform:translateY(-2px);box-shadow:0 22px 44px rgba(243,197,78,.46)}
    .lock{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:#ffe6b0;background:rgba(243,197,78,.12);border:1px solid rgba(243,197,78,.34);padding:9px 17px;border-radius:999px}
    .lock svg{flex:none;color:var(--gold2)}
    .wave{position:absolute;left:0;right:0;bottom:-1px;width:100%;height:72px;display:block;z-index:1}
    .wrap{max-width:1060px;margin:0 auto;padding:34px 24px 8px}
    .sec{margin:0 0 16px;font-size:clamp(20px,2.6vw,27px);color:var(--navy);font-weight:800;letter-spacing:-.015em;text-align:center}
    .parts{list-style:none;padding:0;margin:0 0 40px;display:grid;grid-template-columns:repeat(auto-fill,minmax(278px,1fr));gap:14px}
    .parts a,.parts .pi{display:flex;align-items:center;gap:14px;padding:18px;border:1px solid var(--line);border-radius:16px;background:#fff;color:var(--navy);font-weight:600;box-shadow:0 2px 10px rgba(11,31,56,.04);transition:border-color .14s,transform .14s,box-shadow .14s}
    .parts a:hover{text-decoration:none;border-color:var(--teal);transform:translateY(-3px);box-shadow:0 18px 34px rgba(11,31,56,.12)}
    .parts a::after{content:"\\2192";margin-left:auto;color:var(--teal);font-weight:800;font-size:19px;transition:transform .14s}
    .parts a:hover::after{transform:translateX(3px)}
    .pn{flex:none;display:inline-flex;align-items:center;justify-content:center;min-width:38px;height:38px;padding:0 9px;background:linear-gradient(135deg,#e7f7ec,#d3eede);color:#1f7a44;font-weight:800;font-size:13px;border-radius:11px}
    .ft{background:#fff;border-top:1px solid var(--line);margin-top:24px}
    .ft-in{max-width:1060px;margin:0 auto;padding:30px 24px;display:flex;align-items:center;justify-content:center;text-align:center;gap:14px;color:#8a98a8;font-size:13px;flex-wrap:wrap}
    .ft-in img{height:38px;width:auto;opacity:.9}
    .ov{position:fixed;inset:0;background:rgba(7,18,33,.62);display:none;align-items:center;justify-content:center;padding:20px;z-index:60;backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px)}
    .ov.open{display:flex;animation:seofade .15s ease}
    @keyframes seofade{from{opacity:0}to{opacity:1}}
    .modal{background:#fff;border-radius:24px;max-width:470px;width:100%;padding:36px 32px 30px;text-align:center;box-shadow:0 34px 90px rgba(0,0,0,.45);position:relative;animation:seopop .2s ease}
    @keyframes seopop{from{transform:translateY(10px) scale(.97);opacity:0}to{transform:none;opacity:1}}
    .modal .ic{width:68px;height:68px;border-radius:50%;background:linear-gradient(135deg,#fff1d2,#ffdf9d);color:var(--gold1);display:flex;align-items:center;justify-content:center;margin:0 auto 18px;font-size:32px}
    .modal h3{margin:0 0 10px;font-size:23px;color:var(--navy)}
    .modal p{color:#52617a;font-size:15px;margin:0 0 26px}
    .modal .row{display:flex;flex-direction:column;gap:11px}
    .btn-vip{background:linear-gradient(92deg,var(--gold1),var(--gold2));color:#241803;font-weight:800;padding:15px;border-radius:13px;border:0;cursor:pointer;font-size:15px;text-decoration:none;display:block;box-shadow:0 12px 26px rgba(243,197,78,.32)}
    .btn-vip:hover{text-decoration:none;filter:brightness(1.04)}
    .btn-free{background:#fff;color:var(--teal);font-weight:700;padding:14px;border-radius:13px;border:1px solid #cfe6eb;cursor:pointer;font-size:15px;text-decoration:none;display:block}
    .btn-free:hover{text-decoration:none;background:#f5fcfd}
    .modal .x{position:absolute;top:16px;right:18px;color:#9aa7b6;cursor:pointer;font-size:25px;line-height:1;border:0;background:none}
    @media(max-width:680px){.brand img{height:50px}.nav{padding:14px 18px}.nav-cta{padding:9px 14px}.hero-in{padding:24px 18px 80px}.wrap{padding:28px 18px 8px}.cta{width:100%;justify-content:center}.cta-row{flex-direction:column;align-items:stretch}}
"""

# VIP-only modal markup + behaviour. Placeholders are swapped via str.replace so
# the inline JS braces never collide with f-string/format interpolation.
_SEO_VIP_MODAL = """
  <div class="ov" id="vipov" onclick="if(event.target===this)closeVip()">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="vipt">
      <button class="x" type="button" onclick="closeVip()" aria-label="Close">&times;</button>
      <div class="ic"><svg width="34" height="34" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 20h14a1 1 0 0 0 1-1.21L18.2 9l-4 3.1L12 5.6 9.8 12.1l-4-3.1L4 18.79A1 1 0 0 0 5 20z"/></svg></div>
      <h3 id="vipt">This test is for VIP accounts</h3>
      <p>Sorry, this practice test is currently available to VIP accounts only. Try our free tests now &mdash; or, if you're preparing for the exam and want our premium forecast sets, subscribe to VIP.</p>
      <div class="row">
        <a class="btn-vip" href="__VIP_LINK__">Subscribe to VIP</a>
        <a class="btn-free" href="__APP_LINK__">Try free tests</a>
      </div>
    </div>
  </div>
  <script>
    function showVip(){document.getElementById('vipov').classList.add('open')}
    function closeVip(){document.getElementById('vipov').classList.remove('open')}
    document.addEventListener('keydown',function(e){if(e.key==='Escape')closeVip()});
  </script>
"""


def _seo_cta_block(is_free, app_link, vip_link, label):
    """Return (cta_html, modal_html). Free exams link straight to the app; VIP
    exams turn the CTA into a button that opens the VIP modal."""
    if is_free:
        cta = f'<a class="cta" href="{html_lib.escape(app_link)}">{html_lib.escape(label)} &rarr;</a>'
        return cta, ""
    crown = ('<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
             '<path d="M5 20h14a1 1 0 0 0 1-1.21L18.2 9l-4 3.1L12 5.6 9.8 12.1l-4-3.1L4 18.79A1 1 0 0 0 5 20z"/></svg>')
    cta = (
        f'<button class="cta" type="button" onclick="showVip()">{html_lib.escape(label)} &rarr;</button>'
        f'\n  <span class="lock">{crown} VIP accounts only</span>'
    )
    modal = (_SEO_VIP_MODAL
             .replace("__VIP_LINK__", html_lib.escape(vip_link))
             .replace("__APP_LINK__", html_lib.escape(app_link)))
    return cta, modal


def _seo_hero(app_link, crumb_inner, badge_text, h1_html, lead_html, chips_html, cta_html):
    """Full immersive dark hero: brand nav, breadcrumb, badge, title, lead,
    feature chips, CTA, and an SVG wave dividing into the light content below.
    Page-specific HTML is passed in pre-built/escaped."""
    logo = f"{SEO_APP_URL}/img/logo-ielts.png"
    return (
        '  <header class="hero">\n'
        '    <nav class="nav">'
        f'<a class="brand" href="{SEO_APP_URL}">'
        f'<img src="{logo}" alt="englishoncomputer.com logo" width="62" height="62">'
        '<span class="brand-tx">IELTS<b>Computer</b>Test</span></a>'
        f'<a class="nav-cta" href="{html_lib.escape(app_link)}">Practice now &rarr;</a>'
        '</nav>\n'
        '    <div class="hero-in">\n'
        f'      <nav class="crumb">{crumb_inner}</nav>\n'
        f'      <span class="badge">{badge_text}</span>\n'
        f'      <h1>{h1_html}</h1>\n'
        f'      <p class="lead">{lead_html}</p>\n'
        f'{chips_html}'
        f'      <div class="cta-row">{cta_html}</div>\n'
        '    </div>\n'
        '    <svg class="wave" viewBox="0 0 1440 72" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">'
        '<path fill="#eef3f8" d="M0,34 C300,76 560,6 820,28 C1040,46 1240,80 1440,42 L1440,72 L0,72 Z"></path></svg>\n'
        '  </header>'
    )


def _seo_footer():
    logo = f"{SEO_APP_URL}/img/logo-ielts.png"
    return (
        '  <footer class="ft"><div class="ft-in">'
        f'<img src="{logo}" alt="englishoncomputer.com" width="38" height="38">'
        '<span>&copy; englishoncomputer.com &mdash; IELTS computer-based practice on a 100% real exam interface</span>'
        '</div></footer>'
    )


@router.get("/sitemap-exams.xml", include_in_schema=False)
async def sitemap_exams(request: Request, db: Session = Depends(get_db)):
    base = _seo_base(request)
    rows = []
    for skill, section_type in SEO_SKILL_SECTION.items():
        # Group each part under its exam so the sitemap reads: test → its parts.
        secs_by_exam = {}
        for sec, _exam_title, created in _seo_active_sections(db, section_type):
            secs_by_exam.setdefault(sec.exam_id, []).append((sec, created))
        for exam, _parts in _seo_active_exams(db, section_type):
            ex_lastmod = exam.created_at.date().isoformat() if exam.created_at else None
            rows.append((f"{base}/public/t/{skill}/{exam.exam_id}/{_seo_slugify(exam.title)}", ex_lastmod))
            for sec, created in secs_by_exam.get(exam.exam_id, []):
                p_lastmod = created.date().isoformat() if created else None
                rows.append((f"{base}/public/p/{skill}/{sec.section_id}/{_seo_slugify(sec.part_title)}", p_lastmod))
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
    base = _seo_base(request)
    canonical = f"{base}/public/t/{skill}/{exam_id}/{_seo_slugify(exam.title)}"
    part_titles = [t for _n, t in parts]
    title_tag = f"{exam_title} — IELTS {skill_label} Computer Test | englishoncomputer.com"
    description = (
        f"{exam_title}: practice IELTS {skill_label} on the computer test. "
        f"Parts: {', '.join(part_titles)}. Free practice on a 100% real exam "
        f"interface at englishoncomputer.com."
    )[:300]

    others = []
    for e, _p in _seo_active_exams(db, section_type):
        if e.exam_id != exam_id:
            others.append((e, _seo_slugify(e.title)))
        if len(others) >= 12:
            break

    parts_html = "\n".join(
        f'        <li><span class="pi"><span class="pn">Part {n}</span> {html_lib.escape(t)}</span></li>'
        for n, t in parts
    )
    others_html = "\n".join(
        f'        <li><a href="{base}/public/t/{skill}/{e.exam_id}/{s}">'
        f'{html_lib.escape(_seo_clean(e.title))}</a></li>'
        for e, s in others
    )
    is_free = _seo_exam_is_free(db, exam_id, section_type)
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
        "isAccessibleForFree": is_free,
        "provider": {
            "@type": "EducationalOrganization",
            "name": "englishoncomputer.com",
            "url": SEO_APP_URL,
        },
    }, ensure_ascii=False)

    app_link = f"{SEO_APP_URL}/{skill}_list"
    vip_link = f"{SEO_APP_URL}/vip-packages"
    cta_html, modal_html = _seo_cta_block(
        is_free, app_link, vip_link, "Start practicing on englishoncomputer.com")
    crumb_inner = (
        f'<a href="{SEO_APP_URL}">Home</a> / <a href="{app_link}">IELTS {skill_label}</a>'
        f' / {html_lib.escape(exam_title)}'
    )
    lead_html = (
        f'Practice <strong>{html_lib.escape(exam_title)}</strong> for the IELTS '
        f'{skill_label} section on a 100% real computer-based exam interface.'
    )
    hero = _seo_hero(app_link, crumb_inner, f"IELTS {skill_label} &middot; Computer Test",
                     html_lib.escape(exam_title), lead_html, "", cta_html)
    footer = _seo_footer()
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
  <meta property="og:site_name" content="englishoncomputer.com">
  <meta property="og:image" content="{SEO_APP_URL}/img/logo-ielts.png">
  <script type="application/ld+json">{json_ld}</script>
  <style>{_SEO_CSS}</style>
</head>
<body>
{hero}
  <main class="wrap">
    <h2 class="sec">Parts in this test</h2>
    <ul class="parts">
{parts_html}
    </ul>
    <h2 class="sec">More IELTS {skill_label} tests</h2>
    <ul class="parts">
{others_html}
    </ul>
  </main>
{footer}
{modal_html}
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
    base = _seo_base(request)
    canonical = f"{base}/public/p/{skill}/{section_id}/{_seo_slugify(part_title)}"
    qtypes = [q for q in (sec.question_types or []) if isinstance(q, str)]
    title_tag = f"{part_title} — IELTS {skill_label} Practice | englishoncomputer.com"
    description = (
        f"Practice \"{part_title}\" — Part {sec.order_number} of {exam_title}, "
        f"IELTS {skill_label} computer test. "
        + (f"Question types: {', '.join(qtypes)}. " if qtypes else "")
        + "Free practice on a 100% real exam interface at englishoncomputer.com."
    )[:300]

    # sibling parts of the same exam (internal links)
    siblings = db.query(ExamSection).filter(
        ExamSection.exam_id == sec.exam_id,
        ExamSection.section_type == section_type
    ).order_by(ExamSection.order_number).all()
    siblings_html = "\n".join(
        f'        <li><a href="{base}/public/p/{skill}/{s.section_id}/{_seo_slugify(s.part_title)}">'
        f'<span class="pn">Part {s.order_number}</span> {html_lib.escape(_seo_clean(s.part_title))}</a></li>'
        for s in siblings if _seo_clean(s.part_title) and s.section_id != section_id
    )
    chips_html = (
        '      <div class="chips">'
        + "".join(f'<span class="chip">{html_lib.escape(q)}</span>' for q in qtypes)
        + "</div>\n"
        if qtypes else ""
    )
    is_free = _seo_exam_is_free(db, sec.exam_id, section_type)
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
        "isAccessibleForFree": is_free,
        "provider": {
            "@type": "EducationalOrganization",
            "name": "englishoncomputer.com",
            "url": SEO_APP_URL,
        },
    }, ensure_ascii=False)
    app_link = f"{SEO_APP_URL}/{skill}_list"
    vip_link = f"{SEO_APP_URL}/vip-packages"
    exam_link = f"{base}/public/t/{skill}/{exam.exam_id}/{_seo_slugify(exam.title)}"
    cta_html, modal_html = _seo_cta_block(
        is_free, app_link, vip_link, "Start practicing on englishoncomputer.com")
    crumb_inner = (
        f'<a href="{SEO_APP_URL}">Home</a> / <a href="{app_link}">IELTS {skill_label}</a>'
        f' / <a href="{exam_link}">{html_lib.escape(exam_title)}</a> / Part {sec.order_number}'
    )
    lead_html = (
        f'<strong>{html_lib.escape(part_title)}</strong> is Part {sec.order_number} of '
        f'<a href="{exam_link}">{html_lib.escape(exam_title)}</a> &mdash; IELTS {skill_label} '
        f'on the computer-based test. Practice it on a 100% real exam interface.'
    )
    hero = _seo_hero(app_link, crumb_inner,
                     f"IELTS {skill_label} &middot; Part {sec.order_number}",
                     html_lib.escape(part_title), lead_html, chips_html, cta_html)
    footer = _seo_footer()
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
  <meta property="og:site_name" content="englishoncomputer.com">
  <meta property="og:image" content="{SEO_APP_URL}/img/logo-ielts.png">
  <script type="application/ld+json">{json_ld}</script>
  <style>{_SEO_CSS}</style>
</head>
<body>
{hero}
  <main class="wrap">
    <h2 class="sec">Other parts of {html_lib.escape(exam_title)}</h2>
    <ul class="parts">
{siblings_html}
    </ul>
  </main>
{footer}
{modal_html}
</body>
</html>
"""
    return HTMLResponse(content=page)
