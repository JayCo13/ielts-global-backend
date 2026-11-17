"""Live Action with an Examiner — spoken feedback after each answer (see utils/speaking_live).

The room calls this IN PARALLEL with the normal `/answers` upload, sending the same
recording, so the examiner can start talking without waiting for R2 + Whisper. Nothing is
stored and the official grading is untouched.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import SpeakingAttemptAnswer, User
from app.routes.admin.auth import get_current_student
from app.routes.student.speaking_test import MAX_RECORDING_BYTES, _own_attempt
from app.utils import speaking_audio, speaking_live
from app.utils.speaking_gate import require_speaking_access

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/speaking/live/languages", response_model=dict)
async def live_languages(current_student: User = Depends(get_current_student)):
    """Feedback languages the examiner can speak, in UI-language codes."""
    return {"languages": list(speaking_live.LANGS.keys())}


@router.post("/speaking/live/attempts/{attempt_id}/feedback", response_model=dict)
async def live_feedback(attempt_id: int,
                        order_index: int = Form(...),
                        lang: str = Form('en'),
                        text: Optional[str] = Form(None),
                        audio: Optional[UploadFile] = File(None),
                        current_student: User = Depends(get_current_student),
                        db: Session = Depends(get_db)):
    require_speaking_access(current_student)
    attempt = _own_attempt(db, attempt_id, current_student)
    if attempt.status != 'in_progress':
        raise HTTPException(status_code=409, detail="This test has already ended.")
    answer = db.query(SpeakingAttemptAnswer).filter(
        SpeakingAttemptAnswer.attempt_id == attempt_id,
        SpeakingAttemptAnswer.order_index == order_index).first()
    if not answer:
        raise HTTPException(status_code=404, detail="Question not found in this test.")
    question = answer.question_text or ''
    part = answer.part or 'part1'
    voice = attempt.voice
    db.close()      # nothing else to read; don't hold a pool connection through the AI calls

    lang = lang if lang in speaking_live.LANGS else 'en'
    typed = (text or '').strip()[:3000]
    raw = await audio.read() if audio is not None else b''
    if len(raw) > MAX_RECORDING_BYTES:
        raise HTTPException(status_code=413, detail="The recording is too large.")
    if not raw and not typed:
        raise HTTPException(status_code=400, detail="There is no answer to give feedback on.")

    try:
        speaking_live.take_quota(current_student.user_id)
    except speaking_live.LiveLimit:
        raise HTTPException(status_code=429,
                            detail="You have used today's live examiner feedback. Please come back tomorrow.")

    def work():
        clip, mime = None, 'audio/ogg'
        if raw:
            # Gemini reads Ogg/Opus but not every browser container (Chrome's webm,
            # Safari's mp4), so re-encode exactly like the stored recordings.
            clip, ext = speaking_audio.to_opus(raw)
            if not ext:
                mime = (audio.content_type or 'audio/webm').split(';')[0]
        return speaking_live.feedback(question, part, lang, voice, typed=typed,
                                      audio=clip, audio_mime=mime)

    try:
        return await run_in_threadpool(work)
    except Exception as e:
        speaking_live.give_back(current_student.user_id)
        logger.warning("live feedback failed (attempt %s, q %s): %s", attempt_id, order_index, e)
        # The short reason after the message helps support read a screenshot (no secrets in it).
        reason = type(e).__name__
        if 'HTTP' in str(e):
            reason += ' ' + str(e).split('HTTP', 1)[1][:4].strip()
        raise HTTPException(status_code=502,
                            detail=f"The examiner could not give feedback this time. ({reason})")
