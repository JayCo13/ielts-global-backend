"""Thin Gemini 2.5 Flash client (REST, no SDK dependency). Ported from the VN tree.

Used by the Writing AI grader. Multimodal: sends the essay as text plus any
task-prompt images (charts for Task 1) as inline image parts. Returns parsed JSON
(generationConfig.responseMimeType = application/json). "Think Longer" maps to a
non-zero thinkingBudget.

GEMINI_API_KEY must be set in the backend .env. If it's missing, callers get
GeminiNotConfigured and the route should return 503 (mirrors the GROQ_TRANSLATE
pattern) so the app deploys safely before the key is provisioned.
"""
import os
import json
import base64
import time

import requests

# Flash-Lite has an effectively-unlimited free daily quota (RPD), so it avoids the
# 429 "exceeded quota" the standard Flash free tier hits under real traffic. Use the
# "-latest" ALIAS, not the plain "gemini-2.5-flash-lite" versioned name — the latter
# 404s "no longer available to new users" for newly-created keys. thinkingBudget must
# stay non-zero (small for normal grading, large for Think Longer).
MODEL = "gemini-flash-lite-latest"
_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_ENDPOINT = f"{_BASE}/{MODEL}:generateContent"


def _endpoint(model=None) -> str:
    return f"{_BASE}/{model or MODEL}:generateContent"
# Split, not a single number: grading genuinely can take two minutes, but a *handshake*
# never should. With one flat 120s budget a dropped handshake would burn the whole two
# minutes before the first retry — three attempts could tie up six minutes and still
# grade nothing. (Ported from the VN tree, whose host had a very lossy route to Google.)
_TIMEOUT = (4, 120)     # (connect, read)

# How long to keep re-dialling before giving up on reaching Gemini at all. Separate from
# the three attempts below, which exist for a different reason: those retry answers we
# don't like (429, 5xx, truncated JSON), and repeating those is only worth doing a few
# times. A dropped handshake carries no such cost.
_DIAL_DEADLINE = 30.0


def _post(model=None, **kwargs):
    """POST to Gemini, re-dialling through a lossy path. Only transport failures are
    retried here; anything Gemini actually answers is left to the caller."""
    deadline = time.monotonic() + _DIAL_DEADLINE
    tries = 0
    url = _endpoint(model)
    while True:
        tries += 1
        try:
            return requests.post(url, **kwargs)
        except requests.RequestException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(0.4 * tries, 1.5))


class GeminiNotConfigured(Exception):
    """GEMINI_API_KEY is not set."""


class GeminiError(Exception):
    """Gemini call failed or returned an unparseable response."""


class GeminiQuota(GeminiError):
    """Gemini rejected the call with 429 (rate limit / quota exceeded)."""


def _api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise GeminiNotConfigured("GEMINI_API_KEY is not set")
    return key


def generate_json(system: str, user_text: str, images=None, *,
                  media=None, model: str = None, max_output_tokens: int = 8192,
                  think_longer: bool = False, temperature: float = 0.3,
                  retries: int = 3, busy_backoff: float = 3.0) -> dict:
    """Call Gemini and return the parsed JSON object.

    media: list of (mime_type, raw_bytes) inlined alongside the text — Task 1 charts for
    Writing, a student's recordings for Speaking. `images` is the older name for the same
    argument and still works.
    model: overrides the default Flash-Lite. Speaking grading passes a fuller model
    because it has to listen to audio, not just read text; Flash-Lite stays the default
    for the text-only callers that picked it for its quota headroom.
    max_output_tokens: raise it when the answer is long — a truncated response is invalid
    JSON, which costs a whole retry.
    retries / busy_backoff: how hard to push when Gemini answers "busy" (429/500/503).
    The defaults suit a caller with a person waiting — nginx cuts the request off anyway.
    A background job has nobody waiting and should push much harder: Speaking grading lost
    a whole paper to `503 "model is currently experiencing high demand"` because three
    tries three seconds apart is nothing next to a busy spell.
    """
    media = media if media is not None else images
    key = _api_key()
    parts = [{"text": user_text}]
    for mime, raw in (media or []):
        try:
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode()}})
        except Exception:
            continue  # skip one bad attachment rather than fail the whole grade

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
            # Raise the output ceiling so the large "detailed" JSON isn't truncated
            # (truncation → invalid JSON → unparseable). Flash-Lite allows up to 8192.
            "maxOutputTokens": max_output_tokens,
            # Never 0 (newer Flash rejects it). Small budget normally; large for Think Longer.
            "thinkingConfig": {"thinkingBudget": 8000 if think_longer else 256},
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    # Up to 3 attempts: retries transient 429/5xx AND a 200 whose JSON is truncated/
    # malformed (Flash-Lite occasionally emits invalid JSON on large outputs).
    last_err = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            resp = _post(
                model,
                params={"key": key},
                headers={"Content-Type": "application/json"},
                json=body, timeout=_TIMEOUT,
            )
        except requests.RequestException as e:
            last_err = GeminiError(f"Gemini request failed: {e}")
            if attempt < attempts - 1:
                time.sleep(2); continue
            raise last_err
        if resp.status_code != 200:
            if resp.status_code in (429, 500, 503) and attempt < attempts - 1:
                # Back off progressively: a busy spell does not clear in three seconds.
                time.sleep(busy_backoff * (attempt + 1)); continue
            if resp.status_code == 429:
                raise GeminiQuota(f"Gemini HTTP 429: {resp.text[:300]}")
            raise GeminiError(f"Gemini HTTP {resp.status_code}: {resp.text[:400]}")
        try:
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (KeyError, IndexError, ValueError) as e:
            last_err = GeminiError(f"Gemini returned an unparseable response: {e}")
            if attempt < attempts - 1:
                time.sleep(1); continue
            raise last_err
    raise last_err or GeminiError("Gemini call failed")


def is_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))
