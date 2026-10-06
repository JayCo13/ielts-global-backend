"""Writing AI v2 — grading via Gemini + server-side quota (ported from the VN tree).

- Essay is sent as text; the task prompt's images (Task 1 charts) are inlined so the
  model can judge Task Achievement accurately.
- Detailed feedback (per-sub-criterion explanation / example / strategic advice +
  priority focus) vs OVERVIEW only (scores + one-line comments) — see FREE_GETS_DETAILED.
- Server-side quota: free FREE_DAILY/day, VIP VIP_MONTHLY_MAX/month, fair-usage cap
  per rolling 60 min → cooldown. Every successful AI action logs an AiScoreUsage row.
- All student-facing text and all AI feedback is in ENGLISH (global site).

GLOBAL ENTITLEMENT (differs from VN on purpose — see is_vip()): any active, completed
VIP subscription of ANY package (or role 'student') counts as VIP for Writing AI. This
mirrors the global rule shipped in ielts-tajun d55995f ("unlock AI grading credits for
any active VIP package"). VN only counts single_skill Writing packages.

The legacy Groq endpoint /ai/evaluate-and-save (routes/AI/ai.py) is kept untouched.

On-demand generators (outline / sample essays / key language) and the inline review
UI come in later W3 phases; this file is the grading + quota foundation.
"""
import logging
import os
from typing import Optional
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel
from bs4 import BeautifulSoup
import requests as _requests

from app.database import get_db
from app.models.models import User, WritingTask, WritingAnswer, VIPSubscription, VIPPackage, AiScoreUsage
from app.routes.admin.auth import get_current_student
from app.utils.datetime_utils import get_vietnam_time
from app.utils import gemini_client

logger = logging.getLogger(__name__)

router = APIRouter()


def _ai_http_error(e) -> HTTPException:
    """Map a Gemini failure to a Cloudflare-passable response. 429 (quota) is passed
    through by CF with our JSON body; a raw 502 would be replaced by CF's error page,
    hiding the reason. Quota → 429, everything else → 503."""
    if isinstance(e, gemini_client.GeminiQuota):
        return HTTPException(status_code=429,
                             detail="The AI service is busy or has reached its request limit right now. Please try again in a few minutes.")
    logger.warning("Writing AI (Gemini) error: %s", e)
    return HTTPException(status_code=503, detail="The AI service is temporarily unavailable. Please try again later.")

# GLOBAL: free users previously had 1 full-test + 2 forecast AI evaluations per day
# (client-side counters). The server-side unified daily allowance is 3 so nobody gets
# fewer evaluations than before. VN uses 2.
FREE_DAILY = 3
# GLOBAL: every global user (free or VIP) previously received full feedback (mistakes,
# suggestions, rewritten essay) from the Groq evaluator, so the detailed breakdown stays
# available to free users too. VN shows free users the overview only — set this to False
# to adopt the VN rule. The on-demand generators (outline/sample/keylang/analyze) are new
# features and stay VIP-only as in VN.
FREE_GETS_DETAILED = True
FAIR_WINDOW_MIN = 60
FAIR_MAX = 10          # max grades per rolling 60 min
FAIR_COOLDOWN_MIN = 20  # exceeding the cap pauses grading for 20 min
VIP_MONTHLY_MAX = 600  # VIP: total AI Writing Evaluations per month
FAIR_MSG = ("You have reached the Fair Usage limit. "
            "Please wait 20 minutes before using this feature again.")
MONTHLY_MSG = ("Your account has exceeded the normal monthly usage of AI Writing "
               "Evaluation. The feature will reopen at the start of next month.")
LIMIT_MSG_FREE = "You have used all {n} free AI evaluations for today. Upgrade to VIP for more."
NOT_CONFIGURED_MSG = "AI grading is not configured yet. Please try again later."


def _fair_locked(base_query, now, max_n, cooldown_min) -> bool:
    """Fair-Usage lock: True if the user reached `max_n` uses inside the rolling
    FAIR_WINDOW_MIN and is still within `cooldown_min` minutes of their most recent
    use (a fixed cooldown after hitting the cap, rather than an open-ended block)."""
    window = base_query.filter(AiScoreUsage.created_at >= now - timedelta(minutes=FAIR_WINDOW_MIN))
    if window.count() < max_n:
        return False
    last = base_query.order_by(AiScoreUsage.created_at.desc()).first()
    return bool(last and now < last.created_at + timedelta(minutes=cooldown_min))


def _now():
    return get_vietnam_time().replace(tzinfo=None)


def is_vip(user: User, db: Session) -> bool:
    """VIP for Writing AI purposes (quota + detailed feedback + generators).

    GLOBAL RULE: ANY active, completed VIP subscription — whatever the package (all
    skills, Listening, Reading or Writing) — counts, same as the `is_subscribed` flag of
    /customer/vip/subscription/status that the global frontend used for its AI credits
    (ielts-tajun d55995f). Students always have it.

    VN differs: there only single_skill Writing packages grant Writing VIP
    (app/utils/vip_access.package_covers('writing')). Not applied here so that no global
    paying customer loses the AI credits they currently get."""
    if user.role == "student":
        return True
    return db.query(VIPSubscription.subscription_id).filter(
        VIPSubscription.user_id == user.user_id,
        VIPSubscription.end_date >= _now(),
        VIPSubscription.payment_status == "completed",
    ).first() is not None


def quota_state(user: User, db: Session) -> dict:
    """Free: FREE_DAILY grades/day. VIP: up to VIP_MONTHLY_MAX (600) grades/month plus
    the Fair-Usage cap (10 per rolling 60 min → 20-min cooldown). `month_locked` marks
    a VIP who has used the whole monthly allowance (reopens next month)."""
    now = _now()
    vip = is_vip(user, db)
    # Grade quota counts grade-family actions only (grade / spellcheck / outline /
    # sample / keylang / analyze). Writing Assistant (kind='assist') has its OWN
    # separate limit and must NOT decrement the grade quota.
    q = db.query(AiScoreUsage).filter(
        AiScoreUsage.user_id == user.user_id,
        AiScoreUsage.kind != "assist",
    )
    fair_locked = _fair_locked(q, now, FAIR_MAX, FAIR_COOLDOWN_MIN)
    if vip:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = q.filter(AiScoreUsage.created_at >= month_start).count()
        return {
            "is_vip": True,
            "limit": VIP_MONTHLY_MAX,
            "used": used,
            "remaining": max(0, VIP_MONTHLY_MAX - used),
            "fair_locked": fair_locked,
            "month_locked": used >= VIP_MONTHLY_MAX,
        }
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = q.filter(AiScoreUsage.created_at >= day_start).count()
    return {
        "is_vip": False,
        "limit": FREE_DAILY,
        "used": used,
        "remaining": max(0, FREE_DAILY - used),
        "fair_locked": fair_locked,
        "month_locked": False,
    }


def _enforce_quota(user: User, db: Session) -> dict:
    st = quota_state(user, db)
    if st.get("month_locked"):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=MONTHLY_MSG)
    if st["fair_locked"]:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=FAIR_MSG)
    if not st["is_vip"] and st["remaining"] <= 0:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=LIMIT_MSG_FREE.format(n=st["limit"]))
    return st


def _task_images(instructions: str):
    """Best-effort: pull up to 3 images referenced in the task prompt (charts) for
    the model. Reads local /static files; falls back to HTTP. Never raises."""
    out = []
    if not instructions:
        return out
    try:
        srcs = [img.get("src") for img in BeautifulSoup(instructions, "html.parser").find_all("img")]
    except Exception:
        return out
    import base64 as _b64
    for src in [s for s in srcs if s][:3]:
        raw = None
        mime = "image/png" if src.lower().endswith(".png") else "image/jpeg"
        try:
            if src.startswith("data:"):
                # Inline base64 chart: "data:image/png;base64,AAAA..." (how the
                # authoring tool stores Task 1 charts). MUST be decoded here or the
                # model grades Task 1 blind.
                header, b64 = src.split(",", 1)
                mime = header.split(":", 1)[1].split(";", 1)[0] or mime
                raw = _b64.b64decode(b64)
            elif src.startswith("http"):
                r = _requests.get(src, timeout=15)
                if r.status_code == 200:
                    raw = r.content
            else:
                path = src.lstrip("/")            # "/static/x" -> "static/x" (backend cwd)
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        raw = f.read()
        except Exception:
            raw = None
        if raw:
            out.append((mime, raw))
    return out


_CRITERIA = {
    1: ("IELTS Academic Writing Task 1 (report describing visual data)",
        "Task Achievement (accurately reports key features/trends of the visual, with an overview and data comparisons)"),
    2: ("IELTS Writing Task 2 (essay)",
        "Task Response (fully addresses all parts of the prompt with a clear position and developed ideas)"),
}

# Fixed sub-criteria per criterion (spec "Overall assessment" table). The overview
# gives each of these a 0-9 score with NO explanation; TR differs by task.
_SUBCRITERIA_CC = ["Logical Organization", "Clear Progression of Ideas", "Paragraphing", "Cohesive Devices Usage"]
_SUBCRITERIA_LR = ["Vocabulary Range", "Lexical Accuracy", "Collocation Usage",
                   "Topic-specific Vocabulary", "Word Choice Precision", "Spelling & Word Formation"]
_SUBCRITERIA_GRA = ["Sentence Structure Variety", "Complex Sentence Usage", "Grammar Accuracy",
                    "Verb Tense Consistency", "Article & Preposition Accuracy", "Punctuation Usage"]
_SUBCRITERIA_TR = {
    1: ["Relevance to Prompt", "Overview Quality", "Selection of Key Features", "Data Accuracy",
        "Coverage of Key Features", "Development of Details", "Appropriate Format", "Appropriate Word Count"],
    2: ["Relevance to Prompt", "Addressing All Parts of the Task", "Position Quality", "Development of Ideas",
        "Support with Examples", "Idea Relevance", "Appropriate Essay Structure", "Appropriate Word Count"],
}


def _subcriteria(part_number: int) -> dict:
    return {
        "task_response": _SUBCRITERIA_TR.get(part_number, _SUBCRITERIA_TR[2]),
        "coherence_cohesion": _SUBCRITERIA_CC,
        "lexical_resource": _SUBCRITERIA_LR,
        "grammatical_range": _SUBCRITERIA_GRA,
    }


# VN feedback 23/09: the same essay graded twice produced two different bands. This is NOT
# a new marking criterion and must not be used to raise/lower a band — it is only a
# consistency check that runs AFTER grading, verbatim from that feedback.
_STABILITY = (
    ' SCORING STABILITY & CONSISTENCY (final consistency check — NOT a new marking '
    'criterion, and NEVER a reason to raise or lower a band on purpose). '
    '(1) MINOR ERRORS MUST NOT BE OVER-PENALIZED. Isolated or minor problems — minor '
    'grammatical errors, awkward wording, minor collocation problems, slightly unnatural '
    'expressions, isolated vocabulary problems — must still be identified and explained in '
    'the feedback where relevant, but must NOT reduce the band out of proportion to their '
    'real severity and effect. Judge their weight by frequency, severity, consistency and '
    'impact on communication. A cluster of small errors must NOT automatically drag the '
    'essay into a lower band when the overall quality still matches the higher band. '
    '(2) INTERNAL CONSISTENCY CHECK. Run this step ONLY when the score you first reached '
    'looks unusually low or high for the overall quality of the essay. Then ask: does the '
    'essay really show the core features of the proposed band; are the weaknesses frequent, '
    'severe and consistent enough to support a lower band; are the strengths stable and '
    'clear enough to support a higher band; is the score being driven too much by one '
    'isolated error, one cluster of small errors, or one isolated strength; is the adjacent '
    'band better supported by the Band Descriptors? If the evidence still supports your '
    'first band, KEEP IT. Change it only when there is clear evidence that the first '
    'judgement did not reflect the overall quality. Never adjust a score merely to make the '
    'result "look more stable". '
    '(3) LARGE SCORE DIFFERENCES REQUIRE STRONG EVIDENCE. Where a judgement could produce a '
    'gap of about 1.0 band or more between two markings of the SAME essay, there must be '
    'clear evidence of a genuine difference in overall quality. Do not make a large band '
    'change because of subjective impressions, isolated strengths or weaknesses, minor '
    'language issues, or differing readings of small errors. When the evidence sits between '
    'two bands, weigh in this order: overall pattern of performance -> Band Descriptors -> '
    'frequency, severity and impact of weaknesses. Do not default to the lower band or to '
    'the higher band just because the case is borderline. '
    '(4) DO NOT ARTIFICIALLY RAISE OR LOWER SCORES. This section must never be used to push '
    'bands up, push bands down, favour higher or lower bands, or pull every essay toward one '
    'range. Its only purpose is to make sure the final band is supported by all the evidence '
    'in the essay and matches the Band Descriptors. Do NOT apply rules such as "when in doubt, '
    'round up", "when in doubt, give the higher band", or "when in doubt, give the lower band" — '
    'when uncertain, go back to the overall pattern of performance and the Band Descriptors. '
    'FINAL PRINCIPLE: mark the errors thoroughly, but decide the band on overall quality. '
    'Scoring Stability is a consistency check, not a score-adjustment mechanism.'
)


def _system_prompt(part_number: int, target_band, detailed: bool) -> str:
    task_desc, tr_desc = _CRITERIA.get(part_number, _CRITERIA[2])
    tr_name = "Task Achievement" if part_number == 1 else "Task Response"
    tgt = f" The learner is aiming for band {target_band}; tailor comments/advice toward reaching it (this does NOT change the band you award)." if target_band else ""
    subs = _subcriteria(part_number)
    subs_txt = "; ".join(f"{k}: [{', '.join(v)}]" for k, v in subs.items())
    base = (
        f"You are a strict, experienced IELTS examiner grading a {task_desc}. "
        f"Grade using the official IELTS band descriptors (0-9, halves allowed). "
        f"The four criteria are: {tr_name} ({tr_desc}); Coherence and Cohesion; "
        f"Lexical Resource; Grammatical Range and Accuracy. "
        f"Award each criterion a band 0-9 and compute the overall band as their average rounded to the nearest 0.5.{tgt} "
        f"For EACH criterion also score every fixed sub-criterion (0-9, halves allowed) — {subs_txt}. "
        f"Respond ONLY with a JSON object."
    )
    sub_schema = (
        '"subscores" is a list with one entry per fixed sub-criterion for that criterion, in the given order; '
        '"name" is EXACTLY one of the fixed sub-criterion names above. '
    )
    if detailed:
        schema = (
            ' Schema: {"overall_band": number, "summary": string, '
            '"criteria": {"task_response": {"score": number, "comment": string, "subscores": '
            '[{"name": string, "score": number, "explanation": string, "example": string, "strategic_advice": string}]}, '
            '"coherence_cohesion": {"score": number, "comment": string, "subscores": [...]}, '
            '"lexical_resource": {"score": number, "comment": string, "subscores": [...]}, '
            '"grammatical_range": {"score": number, "comment": string, "subscores": [...]}}, '
            '"priority_focus": [{"level": string, "label": string, "note": string}]}. '
            + sub_schema +
            'This is the "Detailed explanation". "comment" = a short general remark for the criterion. '
            'For EACH sub-criterion present it as Score -> Explanation -> Example -> Strategic Advice: '
            '"explanation" = a concise reason WHY the essay is at that level, based on its ACTUAL features; '
            '"example" = ONE concrete example quoted from the essay illustrating it; '
            '"strategic_advice" = an improvement direction at the CRITERION level to reach a higher band — do NOT fix '
            'individual errors or rewrite sentences here. Use band-aware wording: score 9.0 -> "" (not needed); '
            'score 8.0-8.5 -> refinement toward band 9 (use "refine / increase consistency / improve naturalness / '
            'increase precision"; AVOID "fix errors / correct mistakes"); score 7.0-7.5 -> focus on the remaining '
            'limitations; score <=6.5 -> prioritise the most important problem. '
            '"priority_focus" = the 1-3 MOST important things to improve overall (directional only, NO specific errors/fixes), '
            'each: "level" is "high" (major impact on the band), "medium" (improves stability) or "refinement" '
            '(polish to reach a higher band); "label" = the criterion/sub-criterion name; "note" = one short '
            'directional sentence. Order high -> medium -> refinement. '
            'Write ALL commentary in clear, natural English.'
        )
    else:
        schema = (
            ' Schema: {"overall_band": number, "summary": string, '
            '"criteria": {"task_response": {"score": number, "comment": string, "subscores": [{"name": string, "score": number}]}, '
            '"coherence_cohesion": {"score": number, "comment": string, "subscores": [...]}, '
            '"lexical_resource": {"score": number, "comment": string, "subscores": [...]}, '
            '"grammatical_range": {"score": number, "comment": string, "subscores": [...]}}}. '
            + sub_schema +
            'Keep each comment to one short sentence in English. Do NOT include a detailed error list.'
        )
    return base + schema + _STABILITY


def _plain(html: str) -> str:
    if not html:
        return ""
    try:
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:
        return html


@router.post("/writing/grade/{task_id}")
def grade_writing(
    task_id: int,
    target_band: str = None,
    think_longer: bool = False,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    if not gemini_client.is_configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_MSG)

    st = _enforce_quota(current_student, db)
    detailed = bool(st["is_vip"] or FREE_GETS_DETAILED)

    task = db.query(WritingTask).filter(WritingTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Writing task not found")
    answer = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id, WritingAnswer.user_id == current_student.user_id
    ).first()
    if not answer or not (answer.answer_text or "").strip():
        raise HTTPException(status_code=400, detail="There is no essay to grade yet.")
    if answer.locked:
        raise HTTPException(status_code=403, detail="This essay has been saved to your history and locked. Use 'Retake' to start a new attempt if you want to edit or re-grade it.")

    system = _system_prompt(task.part_number or 2, target_band, detailed)
    user_text = (
        f"TASK PROMPT:\n{_plain(task.instructions)}\n\n"
        f"CANDIDATE ESSAY ({len(answer.answer_text.split())} words):\n{answer.answer_text.strip()}"
    )
    images = _task_images(task.instructions) if (task.part_number == 1) else []

    try:
        # Low temperature: re-grading the same essay must give the same band. The default
        # 0.3 was the clearest source of "one essay, two bands" (VN feedback 23/09); the
        # commentary is still driven by the prompt so it does not get noticeably flatter.
        result = gemini_client.generate_json(system, user_text, images,
                                             think_longer=think_longer, temperature=0.1)
    except gemini_client.GeminiNotConfigured:
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_MSG)
    except gemini_client.GeminiError as e:
        raise _ai_http_error(e)

    crit = result.get("criteria", {}) or {}

    def _cs(key):
        try:
            return float(crit.get(key, {}).get("score"))
        except (TypeError, ValueError):
            return None

    # Persist to WritingAnswer (feeds history + leaderboard).
    answer.score = result.get("overall_band")
    answer.task_achievement_score = _cs("task_response")
    answer.coherence_cohesion_score = _cs("coherence_cohesion")
    answer.lexical_resource_score = _cs("lexical_resource")
    answer.grammatical_range_score = _cs("grammatical_range")
    answer.is_ai_evaluated = True
    answer.updated_at = _now()
    # Marker so a reload (/result) knows this blob has the VIP detailed breakdown.
    result["_detailed"] = detailed
    # Store the full structured result so the review screen can reload it without
    # spending another AI call.
    answer.improvement_suggestions = result
    db.add(AiScoreUsage(user_id=current_student.user_id, kind="grade", created_at=_now()))
    db.commit()

    after = quota_state(current_student, db)
    return {
        "task_id": task_id,
        "part_number": task.part_number,
        "detailed": detailed,
        "result": result,
        "quota": {"remaining": after["remaining"], "limit": after["limit"], "is_vip": after["is_vip"]},
    }


# --------------------------------------------------------------------------
# On-demand generators (VIP only, each counts as 1 AI use) — kept separate from
# grading so free users never trigger them and cost stays controlled.
# --------------------------------------------------------------------------

def _load_task_for_gen(task_id: int, user: User, db: Session):
    """VIP gate + quota + return (task, images). Raises HTTPException otherwise."""
    if not gemini_client.is_configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_MSG)
    if not is_vip(user, db):
        raise HTTPException(status_code=403, detail="This feature is available to VIP accounts only.")
    _enforce_quota(user, db)
    task = db.query(WritingTask).filter(WritingTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Writing task not found")
    images = _task_images(task.instructions) if (task.part_number == 1) else []
    return task, images


def _log_use(user: User, db: Session, kind: str):
    db.add(AiScoreUsage(user_id=user.user_id, kind=kind, created_at=_now()))
    db.commit()


def _save_generated(db: Session, user_id: int, task_id: int, key: str, payload: dict):
    """Persist an on-demand generation onto the user's WritingAnswer so it survives
    edits/reloads (hard-saved). No-op if the user has no answer row yet."""
    ans = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id, WritingAnswer.user_id == user_id
    ).first()
    if not ans:
        return
    data = dict(ans.ai_generated or {})
    data[key] = payload
    ans.ai_generated = data
    db.add(ans)
    db.commit()


@router.post("/writing/outline/{task_id}")
def writing_outline(
    task_id: int, target_band: str = None,
    current_student: User = Depends(get_current_student), db: Session = Depends(get_db),
):
    task, images = _load_task_for_gen(task_id, current_student, db)
    part = task.part_number or 2
    sections = ("Introduction, Overview, Body 1, Body 2" if part == 1
                else "Introduction, Body 1, Body 2, Conclusion")
    tgt = f" targeting band {target_band}" if target_band else ""
    system = (
        f"You are an IELTS Writing tutor. Build a DETAILED, sentence-by-sentence outline{tgt} "
        f"for this {'Task 1 report' if part == 1 else 'Task 2 essay'}. Use exactly these sections: {sections}. "
        f"For EACH section, specify what to write SENTENCE BY SENTENCE as short concrete points — "
        f"say how many sentences and what each one should contain "
        f"{'(with specific figures/comparisons/trends from the chart for Task 1)' if part == 1 else '(clear position, topic sentence, supporting idea, example for Task 2)'}. "
        f"Keep every point concise and easy to follow — NOT full paragraphs. "
        f"Respond ONLY as JSON: {{\"sections\": [{{\"name\": string, \"idea\": string}}]}} where 'idea' contains "
        f"one '- ' bullet line per sentence. Write in clear, natural English."
    )
    user_text = f"TASK PROMPT:\n{_plain(task.instructions)}"
    try:
        result = gemini_client.generate_json(system, user_text, images)
    except gemini_client.GeminiError as e:
        raise _ai_http_error(e)
    resp = {"task_id": task_id, "outline": result}
    _save_generated(db, current_student.user_id, task_id, "outline", resp)
    _log_use(current_student, db, "outline")
    return resp


@router.post("/writing/sample/{task_id}")
def writing_sample(
    task_id: int, variant: str = "target", target_band: str = None,
    current_student: User = Depends(get_current_student), db: Session = Depends(get_db),
):
    """variant='target' → plain model essay at target band; variant='top' → band 8-9
    model essay with advanced vocab <b>, collocations <i>, key structures <u>."""
    task, images = _load_task_for_gen(task_id, current_student, db)
    part = task.part_number or 2
    task_desc = "IELTS Academic Task 1 report" if part == 1 else "IELTS Task 2 essay"
    # Enforce the standard paragraph structure so the sample follows a clean outline
    # (no missing/extra paragraphs) — matches the Suggested Outline.
    structure = ("EXACTLY these paragraphs, one each: Introduction (paraphrase), Overview "
                 "(main trends, no data), Body 1, Body 2 (grouped data comparisons)"
                 if part == 1 else
                 "EXACTLY these paragraphs, one each: Introduction, Body 1, Body 2, Conclusion")
    if variant == "top":
        system = (
            f"Write a complete band 8.0-9.0 model {task_desc} for this task. "
            f"Follow {structure} — do NOT add or omit paragraphs. "
            f"Return ONLY JSON {{\"essay_html\": string}} where the essay is HTML with each paragraph "
            f"in its own <p>, advanced vocabulary wrapped in <b>, collocations in <i>, and key sentence structures in <u>."
        )
    else:
        tgt = target_band or "the target band"
        system = (
            f"Write a complete model {task_desc} at band {tgt} for this task. "
            f"Follow {structure} — do NOT add or omit paragraphs; keep it consistent with a standard outline. "
            f"Return ONLY JSON {{\"essay\": string}} (plain text, paragraphs separated by blank lines, no markup)."
        )
    user_text = f"TASK PROMPT:\n{_plain(task.instructions)}"
    try:
        result = gemini_client.generate_json(system, user_text, images)
    except gemini_client.GeminiError as e:
        raise _ai_http_error(e)
    resp = {"task_id": task_id, "variant": variant, "sample": result}
    _save_generated(db, current_student.user_id, task_id,
                    "sampleTop" if variant == "top" else "sampleTarget", resp)
    _log_use(current_student, db, "sample")
    return resp


@router.post("/writing/keylang/{task_id}")
def writing_keylang(
    task_id: int,
    current_student: User = Depends(get_current_student), db: Session = Depends(get_db),
):
    task, images = _load_task_for_gen(task_id, current_student, db)
    part = task.part_number or 2
    system = (
        f"For a high-band answer to this {'IELTS Task 1' if part == 1 else 'IELTS Task 2'} task, "
        f"list the key language a learner should study. Return ONLY JSON: "
        f"{{\"vocabulary\": [{{\"term\": string, \"note\": string}}], "
        f"\"collocations\": [{{\"term\": string, \"note\": string}}], "
        f"\"patterns\": [{{\"term\": string, \"note\": string}}]}} with 5-10 vocabulary, "
        f"3-5 collocations, 3-5 sentence patterns. 'note' = short meaning/usage in English."
    )
    user_text = f"TASK PROMPT:\n{_plain(task.instructions)}"
    try:
        result = gemini_client.generate_json(system, user_text, images)
    except gemini_client.GeminiError as e:
        raise _ai_http_error(e)
    resp = {"task_id": task_id, "keylang": result}
    _save_generated(db, current_student.user_id, task_id, "keylang", resp)
    _log_use(current_student, db, "keylang")
    return resp


def _words(text) -> int:
    """Count words exactly like the rest of the code base (`len(text.split())`) — two
    different counting rules would make the on-screen number disagree with the check."""
    return len((text or "").split())


def _short_keys(result: dict, min_words: int) -> list:
    """Which complete essay versions are SHORTER than the task's minimum word count."""
    if not isinstance(result, dict):
        return []
    return [k for k in ("revised_essay", "target_band_essay")
            if (result.get(k) or "").strip() and _words(result.get(k)) < min_words]


@router.post("/writing/analyze/{task_id}")
def writing_analyze(
    task_id: int, target_band: str = None,
    current_student: User = Depends(get_current_student), db: Session = Depends(get_db),
):
    """Deep per-paragraph analysis (TR/CC/LR/GR: problem→evidence→explanation→fix) +
    rewrite each paragraph → complete revised essay → (Target-Band version) → Key
    Language. Replaces the old "Sample essay". VIP, 1 AI use."""
    task, images = _load_task_for_gen(task_id, current_student, db)
    answer = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id, WritingAnswer.user_id == current_student.user_id
    ).first()
    if not answer or not (answer.answer_text or "").strip():
        raise HTTPException(status_code=400, detail="There is no essay to analyse yet.")
    part = task.part_number or 2
    tr_name = "Task Achievement" if part == 1 else "Task Response"
    if target_band:
        tgt_block = (
            f' Also produce "target_band_essay": take revised_essay and adjust ONLY the language level to Target Band '
            f'{target_band} (4.0-5.0 simple grammar/basic vocab; 5.5-6.0 intermediate, clarity; 6.5-7.5 advanced but natural; '
            f'8.0-9.0 sophisticated, precise). Keep ideas/data/arguments identical; do not force overly hard language.'
        )
        key_src = "target_band_essay"
    else:
        tgt_block = ' Set "target_band_essay" to null.'
        key_src = "revised_essay"
    # The revised essay is sometimes SHORTER than the original: it is joined from the
    # rewritten paragraphs, and fixing errors mostly trims words — so it can drop below
    # the task minimum (VN feedback 05/10).
    #
    # Expand the paragraph REWRITES themselves rather than padding `revised_essay` alone:
    # the screen shows both side by side and they must stay the same essay.
    min_words = 150 if part == 1 else 250
    also_target = ' and "target_band_essay"' if target_band else ''
    length_block = (
        f' WORD-COUNT FLOOR — check this LAST, after every rewrite is finished, because rewriting '
        f'often removes words: the final "revised_essay"{also_target} must contain at least {min_words} '
        f'words. If it falls short, expand the paragraph rewrites themselves until the joined essay '
        f'reaches {min_words} words, so "paragraphs[].rewrite" and "revised_essay" remain identical in '
        f"content. When expanding: keep the writer's ideas, opinions, key information and overall "
        f'argument unchanged; introduce no new unrelated ideas; develop the EXISTING ideas naturally '
        f'with relevant explanation, detail, examples, comparison or supporting information; keep the '
        f'original style and level; every added sentence must be grammatical, coherent and logically '
        f'connected to the text around it. Do not pad beyond what is needed — the result must read as a '
        f'complete, natural essay, not as sentences bolted on to reach a word count.'
    )
    system = (
        f"You are an expert IELTS Writing tutor. Do a DEEP paragraph-by-paragraph analysis of this "
        f"{'Task 1 report' if part == 1 else 'Task 2 essay'}. Split the candidate essay into its natural paragraphs. "
        f"For EACH paragraph, analyze against the 4 IELTS criteria: TR ({tr_name}), CC (Coherence & Cohesion), "
        f"LR (Lexical Resource), GR (Grammatical Range & Accuracy). For each criterion find ONLY the GENUINE issues "
        f"(do NOT invent issues to fill all 4). Each issue = problem, evidence (EXACT quote from the paragraph), "
        f"explanation (why it hurts that criterion), fix (a specific fix; for LR include the replacement word/phrase; "
        f'for GR the corrected structure). If a criterion has no significant issue, set "ok": true and "issues": []. '
        f"Then rewrite the paragraph applying the fixes: keep the student's ideas & main info, fix TR/CC/LR/GR as needed, "
        f"natural & coherent, do NOT add new ideas or over-upgrade beyond the essay's level. "
        f'Then "revised_essay" = the full essay formed ONLY by joining the paragraph rewrites (do not rewrite anew).{tgt_block}{length_block} '
        f'Then "key_language" extracted ONLY from {key_src}: genuinely useful, reusable items that ACTUALLY appear there — '
        f"do NOT add items not in that version. "
        f'Respond ONLY as JSON: {{"paragraphs": [{{"original": string, "criteria": {{"TR": {{"ok": boolean, "issues": '
        f'[{{"problem": string, "evidence": string, "explanation": string, "fix": string}}]}}, "CC": {{...}}, "LR": {{...}}, '
        f'"GR": {{...}}}}, "rewrite": string}}], "revised_essay": string, "target_band_essay": string, '
        f'"key_language": {{"word_choice": [{{"term": string, "note": string}}], "sentence_structures": '
        f'[{{"term": string, "note": string}}], "cohesion_linking": [{{"term": string, "note": string}}]}}}}. '
        f"Write all commentary in clear, natural English."
    )
    user_text = f"TASK PROMPT:\n{_plain(task.instructions)}\n\nCANDIDATE ESSAY:\n{answer.answer_text.strip()}"
    try:
        result = gemini_client.generate_json(system, user_text, images)
        # Safety net for the word-count floor: the prompt asks for it, but the model still
        # sometimes returns a shorter essay. Recount and ONLY when short make one more call
        # — not charged to the student, since it is our failure. Re-run the whole analysis
        # (not just "make the paragraph longer") so per-paragraph and full essay stay one.
        short = _short_keys(result, min_words)
        if short:
            logger.warning("Writing analyze: %s shorter than %s words (task %s) — retrying once",
                           ", ".join(short), min_words, task_id)
            retry_system = system + (
                f' PREVIOUS ATTEMPT FAILED THE WORD-COUNT FLOOR: {", ".join(short)} came back under '
                f'{min_words} words. This time you MUST reach at least {min_words} words in every final '
                f'essay version, by developing the existing ideas as instructed above.'
            )
            try:
                retry = gemini_client.generate_json(retry_system, user_text, images)
                # Only accept the retry when it is REALLY better: the retry can fail too,
                # and a short essay beats an empty one.
                if isinstance(retry, dict) and retry.get("revised_essay") and \
                        len(_short_keys(retry, min_words)) < len(short):
                    result = retry
                else:
                    logger.warning("Writing analyze: retry still short, keeping first result (task %s)", task_id)
            except gemini_client.GeminiError as e:
                logger.warning("Writing analyze: retry failed (%s), keeping first result (task %s)", e, task_id)
    except gemini_client.GeminiError as e:
        raise _ai_http_error(e)
    resp = {"task_id": task_id, "analysis": result}
    _save_generated(db, current_student.user_id, task_id, "analysis", resp)
    _log_use(current_student, db, "analyze")
    return resp


@router.post("/writing/spellcheck/{task_id}")
def writing_spellcheck(
    task_id: int,
    current_student: User = Depends(get_current_student), db: Session = Depends(get_db),
):
    """Spelling + grammar check only (no band/vocab/coherence). Available to ALL
    users (not VIP-gated), counts as 1 grade-quota AI use."""
    if not gemini_client.is_configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_MSG)
    _enforce_quota(current_student, db)
    task = db.query(WritingTask).filter(WritingTask.task_id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Writing task not found")
    answer = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id, WritingAnswer.user_id == current_student.user_id
    ).first()
    if not answer or not (answer.answer_text or "").strip():
        raise HTTPException(status_code=400, detail="There is no essay to check yet.")
    if answer.locked:
        raise HTTPException(status_code=403, detail="This essay is locked (saved to your history). Use 'Retake' to start a new attempt.")
    system = (
        "You are a proofreader. Check ONLY spelling and grammar in the essay. "
        "Return ONLY JSON: {\"errors\": [{\"original\": string, \"suggestion\": string, \"explain\": string}]}. "
        "'original' = the exact erroneous text span as it appears in the essay; 'suggestion' = the corrected text; "
        "'explain' = a short reason in English. Do NOT comment on vocabulary richness, coherence, ideas, or band. "
        "If there are no errors, return an empty list."
    )
    user_text = f"ESSAY:\n{answer.answer_text.strip()}"
    try:
        result = gemini_client.generate_json(system, user_text)
    except gemini_client.GeminiError as e:
        raise _ai_http_error(e)
    _log_use(current_student, db, "spellcheck")
    after = quota_state(current_student, db)
    return {"task_id": task_id, "errors": result.get("errors", []),
            "quota": {"remaining": after["remaining"], "limit": after["limit"]}}


# --------------------------------------------------------------------------
# Writing Assistant (W2): select text → Check Errors / Ask AI. Separate limit
# from grading: free 20 assist actions/day, VIP unlimited.
# --------------------------------------------------------------------------

ASSIST_FREE_DAILY = 20
ASSIST_FAIR_MAX = 30         # Fair-Usage: max assist calls per rolling 60 min (spec)
ASSIST_FAIR_WINDOW_MIN = 60  # → 20-min cooldown after exceeding (spec)


def _assist_quota(user: User, db: Session) -> dict:
    if not gemini_client.is_configured():
        raise HTTPException(status_code=503, detail=NOT_CONFIGURED_MSG)
    vip = is_vip(user, db)
    now = _now()
    base = db.query(AiScoreUsage).filter(
        AiScoreUsage.user_id == user.user_id, AiScoreUsage.kind == "assist")
    # Fair-Usage applies to everyone (incl. VIP): >30 calls in 60 min → 20-min cooldown.
    if _fair_locked(base, now, ASSIST_FAIR_MAX, FAIR_COOLDOWN_MIN):
        raise HTTPException(status_code=429, detail=FAIR_MSG)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = base.filter(AiScoreUsage.created_at >= day_start).count()
    if not vip and used >= ASSIST_FREE_DAILY:
        raise HTTPException(status_code=429,
                            detail=f"You have used all {ASSIST_FREE_DAILY} AI Assistant requests for today. Upgrade to VIP for unlimited use.")
    return {"is_vip": vip, "remaining": (None if vip else max(0, ASSIST_FREE_DAILY - used))}


class AssistCheck(BaseModel):
    text: str
    part_number: int = 2


class AssistAsk(BaseModel):
    question: str
    context: str = ""
    task_id: Optional[int] = None


@router.post("/writing/assist/check")
def assist_check(
    payload: AssistCheck,
    current_student: User = Depends(get_current_student), db: Session = Depends(get_db),
):
    if not (payload.text or "").strip():
        raise HTTPException(status_code=400, detail="Please select some text first.")
    _assist_quota(current_student, db)
    system = (
        "You are an IELTS writing assistant. Check the given excerpt for issues in four "
        "categories: grammar, vocabulary, context, naturalness. Return ONLY JSON: "
        "{\"errors\": [{\"type\": \"grammar|vocabulary|context|naturalness\", \"error\": string, "
        "\"explain\": string, \"suggestion\": string, \"high_band\": string}]}. "
        "'error' = the problematic part; 'suggestion' = the direct correction (apply-ready); "
        "'high_band' = a more natural, higher-band way to express the same idea; "
        "'explain' = short reason in English. "
        "If the excerpt is already good, return an empty list."
    )
    try:
        result = gemini_client.generate_json(system, f"EXCERPT ({'Task 1' if payload.part_number == 1 else 'Task 2'}):\n{payload.text.strip()}")
    except gemini_client.GeminiError as e:
        raise _ai_http_error(e)
    _log_use(current_student, db, "assist")
    q = _assist_quota_safe(current_student, db)
    return {"errors": result.get("errors", []), "assist_remaining": q}


@router.post("/writing/assist/ask")
def assist_ask(
    payload: AssistAsk,
    current_student: User = Depends(get_current_student), db: Session = Depends(get_db),
):
    if not (payload.question or "").strip():
        raise HTTPException(status_code=400, detail="Please enter a question.")
    _assist_quota(current_student, db)

    # Ground the answer in the CURRENT task (fixes Task 1/Task 2 confusion) and, for
    # Task 1, attach the chart image so questions about the prompt are accurate.
    task_ctx, images = "", []
    if payload.task_id:
        task = db.query(WritingTask).filter(WritingTask.task_id == payload.task_id).first()
        if task:
            task_ctx = f"CURRENT TASK — IELTS Writing Task {task.part_number}:\n{_plain(task.instructions)}"
            if task.part_number == 1:
                images = _task_images(task.instructions)

    system = (
        "You are a professional, concise IELTS Writing tutor. Answer the learner's question "
        "DIRECTLY and on-point, grounded strictly in the CURRENT TASK shown below — never mix up "
        "Task 1 and Task 2, and if asked what the prompt is about, describe THIS task's prompt "
        "(and its chart/image if provided). Be brief: a few sentences, or short '- ' bullet points "
        "when listing. No filler, no restating the question. Write in clear, natural English. "
        "Use **bold** for key terms. "
        "Return ONLY JSON: {\"answer\": string} (the answer may contain newlines and '- ' bullets)."
    )
    parts = []
    if task_ctx:
        parts.append(task_ctx)
    if (payload.context or "").strip():
        parts.append(f"SELECTED EXCERPT FROM THE LEARNER'S ESSAY:\n{payload.context.strip()}")
    parts.append(f"QUESTION:\n{payload.question.strip()}")
    user_text = "\n\n".join(parts)
    try:
        result = gemini_client.generate_json(system, user_text, images)
    except gemini_client.GeminiError as e:
        raise _ai_http_error(e)
    _log_use(current_student, db, "assist")
    q = _assist_quota_safe(current_student, db)
    return {"answer": result.get("answer", ""), "assist_remaining": q}


def _assist_quota_safe(user: User, db: Session):
    """Remaining assist count after logging (None = unlimited/VIP)."""
    vip = is_vip(user, db)
    if vip:
        return None
    now = _now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.query(AiScoreUsage).filter(
        AiScoreUsage.user_id == user.user_id, AiScoreUsage.kind == "assist",
        AiScoreUsage.created_at >= day_start,
    ).count()
    return max(0, ASSIST_FREE_DAILY - used)


def _result_blob(answer: WritingAnswer) -> dict:
    """The stored v2 result, or — for essays graded by the legacy Groq evaluator
    (/ai/evaluate-and-save, whose improvement_suggestions has a different shape) — a
    minimal v2-shaped blob built from the stored scores so the review page renders."""
    blob = answer.improvement_suggestions
    if isinstance(blob, dict) and isinstance(blob.get("criteria"), dict):
        return blob
    return {
        "overall_band": answer.score,
        "summary": "Graded by the previous AI evaluator. Edit your essay and re-evaluate for the new detailed feedback.",
        "criteria": {
            "task_response": {"score": answer.task_achievement_score, "subscores": []},
            "coherence_cohesion": {"score": answer.coherence_cohesion_score, "subscores": []},
            "lexical_resource": {"score": answer.lexical_resource_score, "subscores": []},
            "grammatical_range": {"score": answer.grammatical_range_score, "subscores": []},
        },
        "_detailed": False,
        "_legacy": True,
    }


@router.get("/writing/result/{task_id}")
def writing_result(
    task_id: int,
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """Reload the last stored AI grade for a task (no AI call, no quota)."""
    answer = db.query(WritingAnswer).filter(
        WritingAnswer.task_id == task_id, WritingAnswer.user_id == current_student.user_id
    ).first()
    if not answer or not answer.is_ai_evaluated:
        return {"evaluated": False, "result": None, "generated": (answer.ai_generated if answer else None)}
    return {
        "evaluated": True,
        "result": _result_blob(answer),             # full structured result blob
        "overall_band": answer.score,
        "generated": answer.ai_generated,           # persisted outline/sample/keylang
    }


@router.get("/writing/quota")
def writing_quota(
    current_student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    st = quota_state(current_student, db)
    return {
        "remaining": st["remaining"],
        "limit": st["limit"],
        "used": st["used"],
        "is_vip": st["is_vip"],
        "fair_locked": st["fair_locked"],
        "month_locked": st.get("month_locked", False),
        "configured": gemini_client.is_configured(),
        # Whether a non-VIP grade includes the detailed breakdown (global: yes).
        "free_detailed": FREE_GETS_DETAILED,
    }
