"""Transcribing a student's answer (docs/speaking-spec.md decision #2).

Groq Whisper, not Gemini: the transcript is needed *during* the test — to notice an answer
that is too short (§4.5) and to write the Part 3 AI follow-up from what was actually said
(§4.3) — so latency is what matters here. Gemini still hears the raw audio at grading time,
where pronunciation evidence has to come from the audio itself (§5.3).

A failed transcription is never fatal. The recording is kept regardless and the marker can
work from the audio, so callers get None and carry on rather than losing the answer.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

MODEL = "whisper-large-v3-turbo"
_TIMEOUT = (4, 60)      # (connect, read) — an answer is at most two minutes of audio
_RETRIES = 1


def is_configured() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def transcribe(audio: bytes, filename: str = "answer.webm") -> Optional[str]:
    """Return the spoken text, or None if transcription is unavailable or failed."""
    if not audio or not is_configured():
        return None
    try:
        import groq
        client = groq.Groq(api_key=os.getenv("GROQ_API_KEY"),
                           max_retries=_RETRIES, timeout=_TIMEOUT)
        result = client.audio.transcriptions.create(
            file=(filename, audio),
            model=MODEL,
            language="en",
            # Plain text: nothing downstream uses word timings, and asking for verbose
            # JSON only makes the response bigger on a link that already drops packets.
            response_format="text",
        )
        text = result if isinstance(result, str) else getattr(result, "text", "")
        return (text or "").strip() or None
    except Exception as e:
        logger.warning("Speaking transcription failed: %s", e)
        return None


def looks_too_short(text: Optional[str], part: str) -> bool:
    """Whether Practice Mode should offer a retry (§4.5).

    Deliberately crude — a word count, not a judgement of quality. The nudge is only a
    suggestion the student may ignore, and the real assessment happens at grading.
    """
    words = len((text or "").split())
    floor = {'part1': 12, 'part2': 60, 'part2_followup': 12, 'part3': 20}.get(part, 12)
    return words < floor
