"""Force-align every listening part's transcript to its audio.

Whisper gives word timestamps for what it hears; difflib maps those onto the authored
transcript (exam_sections.description), which is the text `Question.locate` and the
answer keys are written in. The result lets any position in the transcript become an
audio offset — the groundwork for a "play this snippet" button next to each answer.

Ported from the VN tree. Global differences:
  * Audio lives in Cloudflare R2 (`ListeningMedia.audio_url`, public URL); the legacy
    LONGBLOB `audio_file` is only a fallback for old rows. The bytes are pulled from R2
    on demand, capped at MAX_AUDIO_BYTES, and never written to disk.
  * The fingerprint uses the R2 URL (or the blob length for legacy rows) instead of the
    blob length, so the status check never downloads audio. Re-uploading under the same
    filename keeps the same URL, so the audio-replacing endpoints in admin_actions also
    delete the part's alignment row outright.
  * No cron on Koyeb — run on demand from the admin page, or via
    POST /admin/listening-alignment/jobs/run (see listening_alignment_admin.py).

Engine: Groq Whisper (whisper-large-v3-turbo, word timestamps). Key:
GROQ_TRANSLATE_API_KEY, falling back to GROQ_API_KEY. Without either, `_client()` raises
AlignmentUnavailable and the endpoints answer 503 — the app still boots.

Resumable: parts already stored are skipped, so a rate-limit stop can just be re-run.
Read-only against existing data; only writes listening_alignments.

    python -m app.jobs.align_listening_audio
    ... --limit 20        process at most 20 parts
    ... --redo            recompute parts already stored
"""
import difflib
import gzip
import hashlib
import html
import json
import os
import re
import sys
import time

from sqlalchemy.orm import Session

from sqlalchemy import func

from app.models.models import ExamSection, ListeningMedia, ListeningAlignment
from app.utils.datetime_utils import get_vietnam_time

MODEL = "whisper-large-v3-turbo"
MAX_AUDIO_BYTES = 24 * 1024 * 1024      # Groq rejects larger uploads
RATE_LIMIT_SLEEP = 60                    # back-off on 429 before retrying the same part
MAX_RETRIES = 3
_DOWNLOAD_TIMEOUT = (10, 60)             # (connect, read) for the R2 fetch
_GROQ_TIMEOUT = 120                      # seconds; a 24MB part takes a few seconds normally


class AlignmentUnavailable(RuntimeError):
    """No speech-to-text key configured."""


class AudioUnavailable(RuntimeError):
    """The part's audio could not be fetched (missing, too big, R2 error)."""


def _clean(s: str) -> str:
    s = re.sub(r'<[^>]+>', ' ', s or '')
    return re.sub(r'\s+', ' ', html.unescape(s).replace('\xa0', ' ')).strip()


def _tokenize(description: str):
    """Authored words, with the "(mm:ss)" markers admins type stripped out."""
    return [t for t in _clean(description).split(' ')
            if t and not re.fullmatch(r'\((\d{1,2}):(\d{2})\):?', t)]


def _key(w: str) -> str:
    return re.sub(r"[^a-z0-9']", '', w.lower())


def _fingerprint(transcript: str, audio_ref: str) -> str:
    """Identifies the inputs an alignment was built from.

    Hashes the transcript text plus an identifier of the audio: the R2 URL, or for a
    legacy LONGBLOB row its byte length (read with SQL LENGTH()) — so the status check
    never has to download audio just to decide there is nothing to do.
    """
    payload = f"{_clean(transcript)}|{audio_ref}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def audio_info(db: Session, section_id: int):
    """(audio_ref, known_size) for a part without loading any audio, or (None, None).

    known_size is the blob length for legacy rows; None for R2 (unknown until fetched).
    """
    row = (
        db.query(ListeningMedia.audio_url, func.length(ListeningMedia.audio_file))
        .filter(ListeningMedia.section_id == section_id)
        .first()
    )
    if not row:
        return None, None
    url, blob_len = row
    if url:
        return f"r2:{url}", None
    if blob_len:
        return f"blob:{int(blob_len)}", int(blob_len)
    return None, None


def load_audio(media: ListeningMedia):
    """(bytes, upload_name) for one part. Raises AudioUnavailable."""
    if media.audio_url:
        import requests
        try:
            with requests.get(media.audio_url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as resp:
                if resp.status_code != 200:
                    raise AudioUnavailable(f"R2 trả về HTTP {resp.status_code}")
                buf = bytearray()
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    buf.extend(chunk)
                    if len(buf) > MAX_AUDIO_BYTES:
                        raise AudioUnavailable("File audio vượt 24MB")
        except requests.RequestException as exc:
            raise AudioUnavailable(f"Không tải được audio từ R2: {str(exc)[:120]}")
        if not buf:
            raise AudioUnavailable("File audio rỗng")
        source_name = media.audio_url.split('?')[0]
        data = bytes(buf)
    elif media.audio_file:
        if len(media.audio_file) > MAX_AUDIO_BYTES:
            raise AudioUnavailable("File audio vượt 24MB")
        source_name = media.audio_filename or ''
        data = media.audio_file
    else:
        raise AudioUnavailable("Part chưa có file audio")

    # Groq picks the decoder from the extension, so keep the real one.
    ext = os.path.splitext(source_name)[1].lower()
    if not re.fullmatch(r'\.(mp3|m4a|wav|ogg|webm|flac|mp4|mpeg|mpga|opus)', ext or ''):
        ext = '.mp3'
    return data, f"part_{media.section_id}{ext}"


def _client():
    key = os.getenv('GROQ_TRANSLATE_API_KEY') or os.getenv('GROQ_API_KEY')
    if not key:
        raise AlignmentUnavailable("GROQ_TRANSLATE_API_KEY / GROQ_API_KEY chưa được cấu hình")
    import groq
    return groq.Groq(api_key=key, timeout=_GROQ_TIMEOUT)


def align_one(client, tokens, audio_bytes, name):
    """Return (times, duration) — times parallel to tokens, None where unmatched."""
    resp = client.audio.transcriptions.create(
        file=(name, audio_bytes),
        model=MODEL,
        response_format="verbose_json",
        timestamp_granularities=["word"],
    )
    words = getattr(resp, 'words', None) or []
    asr = [w['word'] if isinstance(w, dict) else w.word for w in words]
    starts = [w['start'] if isinstance(w, dict) else w.start for w in words]

    matcher = difflib.SequenceMatcher(None, [_key(t) for t in tokens],
                                      [_key(t) for t in asr], autojunk=False)
    times = [None] * len(tokens)
    for i, j, n in matcher.get_matching_blocks():
        for d in range(n):
            times[i + d] = round(float(starts[j + d]), 2)
    return times, float(getattr(resp, 'duration', 0) or 0)


def store_alignment(db: Session, section_id: int, tokens, times, duration, fingerprint):
    """Write (or replace) one part's alignment row and commit. Returns coverage %."""
    idxs = [i for i, t in enumerate(times) if t is not None]
    span = (idxs[-1] - idxs[0] + 1) if idxs else 0
    coverage = round(len(idxs) * 100 / span, 1) if span else 0.0

    db.merge(ListeningAlignment(
        section_id=section_id,
        audio_duration=duration,
        token_count=len(tokens),
        aligned_count=len(idxs),
        coverage_pct=coverage,
        model=MODEL,
        source_fingerprint=fingerprint,
        data_gz=gzip.compress(json.dumps({'tokens': tokens, 'times': times},
                                         ensure_ascii=False).encode('utf-8')),
        created_at=get_vietnam_time().replace(tzinfo=None),
    ))
    db.commit()
    return coverage


def run(db: Session, limit=None, redo=False, exam_id=None, sleep_on_rate_limit=True) -> dict:
    client = _client()
    existing = {} if redo else {
        row[0]: row[1] for row in
        db.query(ListeningAlignment.section_id, ListeningAlignment.source_fingerprint).all()
    }

    query = db.query(ExamSection.section_id, ExamSection.description).filter(
        ExamSection.section_type == 'listening')
    if exam_id is not None:
        query = query.filter(ExamSection.exam_id == exam_id)
    sections = query.order_by(ExamSection.section_id).all()

    stats = {'aligned': 0, 'realigned': 0, 'skipped_done': 0, 'backfilled_fingerprint': 0,
             'no_transcript': 0, 'no_audio': 0, 'too_big': 0, 'failed': 0, 'coverage': []}
    processed = 0

    for section_id, description in sections:
        tokens = _tokenize(description)
        if len(tokens) < 50:
            stats['no_transcript'] += 1
            continue

        audio_ref, known_size = audio_info(db, section_id)
        if not audio_ref:
            stats['no_audio'] += 1
            continue
        if known_size and known_size > MAX_AUDIO_BYTES:
            stats['too_big'] += 1
            continue

        fingerprint = _fingerprint(description, audio_ref)
        if section_id in existing:
            stored = existing[section_id]
            if stored == fingerprint:
                stats['skipped_done'] += 1
                continue
            if stored is None:
                # Aligned before fingerprints existed. Assume it still matches its source
                # rather than paying to redo the whole library on this one upgrade.
                db.query(ListeningAlignment).filter(
                    ListeningAlignment.section_id == section_id
                ).update({'source_fingerprint': fingerprint})
                db.commit()
                stats['backfilled_fingerprint'] += 1
                continue
            stats['realigned'] += 1   # transcript edited or audio replaced

        media = db.query(ListeningMedia).filter(ListeningMedia.section_id == section_id).first()
        if not media:
            stats['no_audio'] += 1
            continue
        try:
            audio_bytes, name = load_audio(media)
        except AudioUnavailable as exc:
            msg = str(exc)
            print(f"  [{section_id}] audio: {msg}", flush=True)
            stats['too_big' if '24MB' in msg else 'no_audio'] += 1
            continue

        times = None
        for attempt in range(MAX_RETRIES):
            try:
                times, duration = align_one(client, tokens, audio_bytes, name)
                break
            except Exception as exc:
                msg = str(exc)
                if ('429' in msg or 'rate' in msg.lower()) and sleep_on_rate_limit:
                    print(f"  [{section_id}] rate limit, chờ {RATE_LIMIT_SLEEP}s "
                          f"(lần {attempt + 1}/{MAX_RETRIES})", flush=True)
                    time.sleep(RATE_LIMIT_SLEEP)
                    continue
                print(f"  [{section_id}] LỖI: {msg[:120]}", flush=True)
                times = None
                break
        del audio_bytes

        if times is None:
            stats['failed'] += 1
            continue

        coverage = store_alignment(db, section_id, tokens, times, duration, fingerprint)

        stats['aligned'] += 1
        stats['coverage'].append(coverage)
        processed += 1
        if processed % 20 == 0:
            print(f"  ...{processed} part xong", flush=True)
        if limit and processed >= limit:
            break

    cov = stats.pop('coverage')
    if cov:
        cov.sort()
        stats['coverage_median'] = cov[len(cov) // 2]
        stats['coverage_min'] = cov[0]
        stats['coverage_under_60pct'] = sum(1 for c in cov if c < 60)
    return stats


def main():
    from app.database import SessionLocal
    limit = None
    redo = '--redo' in sys.argv
    if '--limit' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--limit') + 1])
    db = SessionLocal()
    started = time.time()
    try:
        result = run(db, limit=limit, redo=redo)
        result['elapsed_sec'] = round(time.time() - started)
        print("Align listening audio:", result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
