"""Where Speaking recordings live in the global stack (Cloudflare R2, private).

The VN tree writes each answer to `media/speaking/<attempt>/...` on the VPS disk, outside
the publicly mounted `static/` tree. Global runs on Koyeb, whose disk is wiped on every
redeploy/cold start, so recordings go to R2 instead (see `r2_storage.put_private_object`).

Privacy model:
  * the browser never sees an R2 URL — playback goes through
    `/student/speaking/test/recordings/{answer_id}`, which checks ownership and streams the
    bytes from R2;
  * the key carries 192 random bits (`secrets.token_urlsafe(24)`), so even though the main
    bucket also has a public r2.dev domain, a recording cannot be enumerated or guessed;
  * set `R2_SPEAKING_BUCKET` to a bucket with no public access to remove even that.

What is stored in `SpeakingAttemptAnswer.first_audio/retry_audio` is the R2 key (always
starting with `KEY_PREFIX`). Nothing here raises: storage problems are logged and reported
as "not stored", because losing the audio must never lose the student's answer (the
transcript is still saved).
"""
import logging
import os
import secrets

logger = logging.getLogger(__name__)

KEY_PREFIX = "speaking/recordings/"
GATE_DEBUG_PREFIX = "speaking/_gate_debug/"

MIME_BY_EXT = {'.ogg': 'audio/ogg', '.webm': 'audio/webm', '.m4a': 'audio/mp4',
               '.mp4': 'audio/mp4', '.mp3': 'audio/mpeg', '.wav': 'audio/wav',
               '.aac': 'audio/aac', '.flac': 'audio/flac'}


def is_configured() -> bool:
    try:
        from app.utils.r2_storage import r2_configured
        return r2_configured()
    except Exception:           # boto3 missing in a dev environment, etc.
        return False


def mime_for(key: str, default: str = 'audio/ogg') -> str:
    return MIME_BY_EXT.get(os.path.splitext(key or '')[1].lower(), default)


def recording_key(attempt_id: int, answer_id: int, retry: bool, ext: str) -> str:
    """speaking/recordings/<attempt>/<answer>[-retry]-<192 random bits><ext>."""
    ext = ext if ext in MIME_BY_EXT else '.webm'
    token = secrets.token_urlsafe(24)
    return f"{KEY_PREFIX}{attempt_id}/{answer_id}{'-retry' if retry else ''}-{token}{ext}"


def save(key: str, data: bytes) -> bool:
    """Upload one recording. False (logged) when R2 is not configured or the call fails."""
    if not data:
        return False
    if not is_configured():
        logger.warning("Speaking recording not stored: R2 is not configured (%s)", key)
        return False
    try:
        from app.utils.r2_storage import put_private_object
        put_private_object(key, data, mime_for(key))
        return True
    except Exception as e:
        logger.warning("Speaking recording upload failed (%s): %s", key, e)
        return False


def load(key: str):
    """Bytes of one recording, or None when it was pruned / never stored / R2 fails."""
    if not key:
        return None
    if not key.startswith(KEY_PREFIX):
        # Not an R2 key (e.g. a VN disk path copied over in a dump): never read arbitrary
        # local paths from a DB value — treat it as an expired recording.
        return None
    if not is_configured():
        return None
    from app.utils.r2_storage import get_private_object
    return get_private_object(key) or None


def delete(key: str) -> bool:
    if not key or not key.startswith(KEY_PREFIX) or not is_configured():
        return False
    from app.utils.r2_storage import delete_private_object
    return delete_private_object(key)


def save_gate_debug(raw: bytes, name: str) -> None:
    """Testers-only diagnostic copy of a practice recording (VN: media/speaking/_gate_debug)."""
    if not raw or not is_configured():
        return
    try:
        from app.utils.r2_storage import put_private_object
        put_private_object(GATE_DEBUG_PREFIX + name, raw, 'audio/ogg')
    except Exception as e:
        logger.warning("Could not store gate debug recording: %s", e)
