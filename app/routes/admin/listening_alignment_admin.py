"""Admin: audio timestamps for a listening test.

Aligning a part's transcript to its audio is what powers the student's "replay the
moment this answer was spoken" button. Ported from the VN tree; global has no cron, so
parts are aligned on demand from the admin page (or POST /listening-alignment/jobs/run).
Audio lives in R2 (`ListeningMedia.audio_url`); see app/jobs/align_listening_audio.py.
"""
import io
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from pydantic import BaseModel

from app.models.models import (
    Exam, ExamSection, ListeningMedia, ListeningAlignment, ListeningCueOverride,
    Question, User,
)
from app.routes.admin.auth import get_current_admin
from app.jobs.align_listening_audio import (
    MAX_AUDIO_BYTES, AlignmentUnavailable, AudioUnavailable, _client, _fingerprint,
    _tokenize, align_one, audio_info, load_audio, store_alignment,
    run as run_alignment_job,
)
from app.utils.audio_cues import build_cues, suggest_locates
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()


def _state(db: Session, section: ExamSection) -> dict:
    """One part's alignment state, including whether its source has since changed."""
    tokens = _tokenize(section.description)
    audio_ref, known_size = audio_info(db, section.section_id)
    row = db.query(ListeningAlignment).filter(
        ListeningAlignment.section_id == section.section_id).first()

    if len(tokens) < 50:
        status, note = 'no_transcript', 'Part chưa có transcript (hoặc quá ngắn)'
    elif not audio_ref:
        status, note = 'no_audio', 'Part chưa có file audio'
    elif known_size and known_size > MAX_AUDIO_BYTES:
        status, note = 'audio_too_big', 'File audio vượt 24MB, không gửi đi gióng được'
    elif not row:
        status, note = 'missing', 'Chưa tạo mốc thời gian'
    elif row.source_fingerprint != _fingerprint(section.description, audio_ref):
        status, note = 'stale', 'Transcript hoặc audio đã đổi sau lần gióng gần nhất'
    else:
        status, note = 'ok', 'Đã có mốc thời gian'

    return {
        'section_id': section.section_id,
        'part_number': section.order_number,
        'status': status,
        'note': note,
        'coverage_pct': row.coverage_pct if row else None,
        'token_count': row.token_count if row else None,
        'aligned_at': row.created_at.isoformat() if row and row.created_at else None,
        'can_run': status in ('missing', 'stale', 'ok'),
    }


@router.get("/listening-alignment/exam/{exam_id}", response_model=dict)
async def alignment_status(
    exam_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    exam = db.query(Exam).filter(Exam.exam_id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    sections = (
        db.query(ExamSection)
        .filter(ExamSection.exam_id == exam_id, ExamSection.section_type == 'listening')
        .order_by(ExamSection.order_number).all()
    )
    return {"exam_id": exam_id, "title": exam.title,
            "parts": [_state(db, s) for s in sections]}


@router.post("/listening-alignment/section/{section_id}/run", response_model=dict)
def run_alignment(
    section_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Align one part now. Takes a couple of seconds; safe to repeat.

    Plain `def` (not async): the R2 download and Whisper call block, so FastAPI runs
    this in its threadpool instead of stalling the event loop."""
    section = db.query(ExamSection).filter(
        ExamSection.section_id == section_id,
        ExamSection.section_type == 'listening').first()
    if not section:
        raise HTTPException(status_code=404, detail="Listening part not found")

    tokens = _tokenize(section.description)
    if len(tokens) < 50:
        raise HTTPException(status_code=400, detail="Part chưa có transcript để gióng")

    media = db.query(ListeningMedia).filter(
        ListeningMedia.section_id == section_id).first()
    if not media:
        raise HTTPException(status_code=400, detail="Part chưa có file audio")
    try:
        audio_bytes, name = load_audio(media)
    except AudioUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        times, duration = align_one(_client(), tokens, audio_bytes, name)
    except AlignmentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gióng thất bại: {str(exc)[:200]}")

    audio_ref, _ = audio_info(db, section_id)
    store_alignment(db, section_id, tokens, times, duration,
                    _fingerprint(section.description, audio_ref))
    return {"section_id": section_id, "part_number": section.order_number,
            **_state(db, section)}


@router.get("/listening-alignment/section/{section_id}/suggest-locate", response_model=dict)
async def suggest_locate(
    section_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Propose the transcript sentence that holds each answer, for the locate field.

    Read-only — nothing is written until the admin accepts a suggestion and saves the
    part as usual.
    """
    section = db.query(ExamSection).filter(
        ExamSection.section_id == section_id,
        ExamSection.section_type == 'listening').first()
    if not section:
        raise HTTPException(status_code=404, detail="Listening part not found")

    rows = suggest_locates(db, section_id)
    if not rows:
        raise HTTPException(
            status_code=400,
            detail="Part chưa có mốc thời gian — hãy bấm 'Tạo mốc' trước.")

    return {
        "section_id": section_id,
        "part_number": section.order_number,
        "questions": rows,
        "suggested": sum(1 for r in rows if r["suggestion"]),
        "total": len(rows),
    }


class CueOverrideIn(BaseModel):
    start: float
    end: float


@router.get("/listening-alignment/section/{section_id}/cues", response_model=dict)
async def list_cues(
    section_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Every question of a part with the timestamp students will hear, and where it
    came from: an admin's own value, the locate excerpt, the answer word, or nothing."""
    section = db.query(ExamSection).filter(
        ExamSection.section_id == section_id,
        ExamSection.section_type == 'listening').first()
    if not section:
        raise HTTPException(status_code=404, detail="Listening part not found")

    cues = {c['question_id']: c for c in build_cues(db, section_id)}
    questions = (
        db.query(Question)
        .filter(Question.section_id == section_id, Question.question_type != 'main_text')
        .order_by(Question.question_id).all()
    )
    row = db.query(ListeningAlignment).filter(
        ListeningAlignment.section_id == section_id).first()

    out = []
    for order, q in enumerate(questions, start=1):
        cue = cues.get(q.question_id) or {}
        out.append({
            'question_id': q.question_id,
            'order': order,
            'answer': (q.correct_answer or '')[:80],
            'start': cue.get('start'),
            'end': cue.get('end'),
            'source': cue.get('source', 'none'),
        })
    return {
        'section_id': section_id,
        'part_number': section.order_number,
        'audio_duration': row.audio_duration if row else None,
        'questions': out,
        'with_cue': sum(1 for q in out if q['start'] is not None),
        'total': len(out),
    }


@router.put("/listening-alignment/question/{question_id}/cue", response_model=dict)
async def set_cue(
    question_id: int,
    payload: CueOverrideIn,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Pin a timestamp by hand. Survives every future re-alignment."""
    question = db.query(Question).filter(Question.question_id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    if payload.end <= payload.start:
        raise HTTPException(status_code=400, detail="Thời điểm kết thúc phải sau thời điểm bắt đầu")

    db.merge(ListeningCueOverride(
        question_id=question_id,
        section_id=question.section_id,
        start_time=max(0.0, float(payload.start)),
        end_time=float(payload.end),
        updated_by=current_admin.user_id,
        updated_at=get_vietnam_time().replace(tzinfo=None),
    ))
    db.commit()
    return {"question_id": question_id, "start": payload.start, "end": payload.end,
            "source": "manual"}


@router.delete("/listening-alignment/question/{question_id}/cue", response_model=dict)
async def clear_cue(
    question_id: int,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Drop the manual pin and fall back to whatever the automatic match finds."""
    db.query(ListeningCueOverride).filter(
        ListeningCueOverride.question_id == question_id).delete()
    db.commit()
    return {"question_id": question_id, "cleared": True}


@router.get("/listening-alignment/section/{section_id}/audio")
async def stream_part_audio(
    section_id: int,
    request: Request,
    token: str = None,
    db: Session = Depends(get_db),
):
    """Stream a part's audio for the admin review screen.

    The student route refuses admin accounts, and an <audio> element can't send an
    Authorization header, so the token rides in the query string — same approach the
    student player already uses. Range requests are honoured so the player can seek
    straight to a timestamp instead of downloading the whole file first.
    """
    from jose import jwt, JWTError
    from app.routes.admin.auth import SECRET_KEY, ALGORITHM

    if not token:
        auth = request.headers.get("authorization", "")
        token = auth.replace("Bearer ", "") if auth else None
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.username == username).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    media = db.query(ListeningMedia).filter(
        ListeningMedia.section_id == section_id).first()
    if media and media.audio_url:
        # Global stores audio in R2 under a public URL; the browser can seek on it
        # directly (R2 honours Range), so just hand the player that URL.
        return RedirectResponse(media.audio_url, status_code=302)
    if not media or not media.audio_file:
        raise HTTPException(status_code=404, detail="Part chưa có audio")

    blob = media.audio_file
    size = len(blob)
    start, end, status_code = 0, size - 1, 200
    range_header = request.headers.get("Range")
    if range_header:
        m = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if m:
            start = int(m.group(1))
            if m.group(2):
                end = int(m.group(2))
            status_code = 206
    end = min(end, size - 1)
    chunk = blob[start:end + 1]

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(len(chunk)),
        "Content-Disposition": f"inline; filename=part_{section_id}.mp3",
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    return StreamingResponse(io.BytesIO(chunk), status_code=status_code,
                             media_type="audio/mpeg", headers=headers)


@router.post("/listening-alignment/jobs/run", response_model=dict)
def run_alignment_batch(
    exam_id: int = None,
    limit: int = 10,
    redo: bool = False,
    current_admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Align parts that are missing or stale (global replacement for VN's nightly cron).

    Bounded by `limit` so one request stays short; call again to continue. Never sleeps
    on a rate limit inside the request — a 429 just ends the batch early.
    """
    try:
        _client()
    except AlignmentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return run_alignment_job(db, limit=max(1, min(limit, 50)), redo=redo,
                             exam_id=exam_id, sleep_on_rate_limit=False)
