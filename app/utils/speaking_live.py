"""Live Action with an Examiner (brief "5 Speaking Live action", 2026-11-14).

After each answer, the examiner finds the 1-2 most noticeable mistakes and says them out
loud, short and funny, in the language the student picked. This is a side channel only:
  * nothing here scores the answer or touches `speaking_grade` (§7 of the brief) — the
    normal grading still runs on Submit, from the recordings the room already uploads;
  * nothing is stored (§8). The feedback text and audio go back in the response and are
    gone when the session ends.

Speed is the main constraint ("không được để thời gian phân tích làm gián đoạn"). So:
  * one Gemini Flash-Lite call that listens to the answer directly. Whisper's transcript
    normalises mispronunciations away ("lay" for "play" comes out as "play"), and the
    brief's best jokes are about pronunciation, so the model needs the audio;
  * a small thinking budget and a short output (≤ ~120 words, under 60 seconds spoken);
  * one TTS call. Google Chirp3-HD is used when Google TTS is configured: it is expressive
    and speaks most of the UI languages. Gemini TTS is the fallback, then no audio at all
    (the room shows the text and waits for the reading time instead).

Env:
  SPEAKING_LIVE_MODEL        Gemini model for the analysis (default: gemini-flash-lite-latest)
  SPEAKING_LIVE_DAILY_LIMIT  feedbacks per user per day, per worker (default 150)
"""
import base64
import logging
import os
import threading
import time
import unicodedata
from datetime import date

logger = logging.getLogger(__name__)

# Flash, not Flash-Lite: Flash-Lite wrote stiff, translated-sounding Vietnamese (feedback
# 2026-11-17 "câu từ quá máy móc"). Set SPEAKING_LIVE_MODEL=gemini-flash-lite-latest to go back.
MODEL = os.getenv("SPEAKING_LIVE_MODEL") or "gemini-flash-latest"
# Which TTS speaks first: "gemini" (default; far more natural and emotional, handles English
# words inside another language) or "google" (Chirp3-HD; faster, flatter). The other one is
# the fallback either way.
TTS_FIRST = (os.getenv("SPEAKING_LIVE_TTS") or "gemini").strip().lower()
DAILY_LIMIT = int(os.getenv("SPEAKING_LIVE_DAILY_LIMIT", "150"))
MAX_WORDS = 120

# UI language code → (name used in the prompt, Google TTS locale or None).
# None = Chirp3-HD has no voice for it; the Gemini TTS fallback is multilingual.
LANGS = {
    'en': ('English', None),            # English uses the examiner's own accent, see below
    'vi': ('Vietnamese', 'vi-VN'),
    'hi': ('Hindi', 'hi-IN'),
    'zh': ('Simplified Chinese (Mandarin)', 'cmn-CN'),
    'ko': ('Korean', 'ko-KR'),
    'ja': ('Japanese', 'ja-JP'),
    'ms': ('Malay', None),
    'id': ('Indonesian', 'id-ID'),
    'th': ('Thai', 'th-TH'),
    'tl': ('Filipino', None),
    'bn': ('Bengali', 'bn-IN'),
    'ur': ('Urdu', 'ur-IN'),
    'ar': ('Arabic', 'ar-XA'),
    'ru': ('Russian', 'ru-RU'),
    'es': ('Spanish', 'es-ES'),
    'pt': ('Portuguese (Brazil)', 'pt-BR'),
    'fr': ('French', 'fr-FR'),
    'de': ('German', 'de-DE'),
    'tr': ('Turkish', 'tr-TR'),
    'fa': ('Persian', None),
    'ne': ('Nepali', None),
    'si': ('Sinhala', None),
    'my': ('Burmese', None),
    'km': ('Khmer', None),
}

MOODS = ('amused', 'shocked', 'exasperated', 'impressed', 'cheerful', 'sarcastic')


class LiveLimit(Exception):
    """The per-day cap for this user was reached."""


# ── Per-user daily cap (in memory) ────────────────────────────────────────────────────
# Nothing is stored for this feature (§8), so the cap lives in memory. Two workers means
# a student could get up to 2× the limit; that is fine for a cost guard.
_lock = threading.Lock()
_usage = {}          # (user_id, date) -> count


def take_quota(user_id: int):
    today = date.today()
    with _lock:
        for k in [k for k in _usage if k[1] != today]:
            _usage.pop(k, None)
        n = _usage.get((user_id, today), 0)
        if n >= DAILY_LIMIT:
            raise LiveLimit()
        _usage[(user_id, today)] = n + 1


def give_back(user_id: int):
    today = date.today()
    with _lock:
        if _usage.get((user_id, today), 0) > 0:
            _usage[(user_id, today)] -= 1


# ── Analysis ──────────────────────────────────────────────────────────────────────────

_SYSTEM = """You are a REAL IELTS Speaking examiner with a big personality, giving live spoken feedback right after a candidate answers one question. You are an expert, blunt, and genuinely funny: sometimes surprised, sometimes helpless, sarcastic or cheerful. You are NOT a polite traditional teacher.

TASK
- Listen to the candidate's answer (audio, and/or the typed text).
- Pick only the 1-2 MOST noticeable mistakes: ones that clearly hurt the answer or the band, that the learner can easily notice and remember, and that can be fixed with one concrete example. Consider Fluency and Coherence, Lexical Resource, Grammatical Range and Accuracy, and Pronunciation, but never go through all four. Ignore small slips.
- Pronunciation: only mention a sound you clearly HEAR wrong in the audio (e.g. dropped /p/ in "play" -> "lay", "go" said like "gao"). Never invent pronunciation errors; with no audio, do not comment on pronunciation.
- For each mistake, roast it with a short, funny, slightly brutal remark tied DIRECTLY to what they actually said (exaggeration, comparison, wordplay, playful helplessness), quote their words, then give the correct version they can use immediately.
- Match the sarcasm to the mistake: an obvious mistake earns a stronger reaction.
- If the answer is genuinely good, praise it with personality (funny, specific), not "good job". Do not force a joke when there is nothing to joke about.
- If the candidate said almost nothing or went off-topic, react to THAT with humour and tell them what a real answer needs.

STYLE: Short, Sharp, Funny & Memorable
- Structure (flexible, not every part is needed): one quick reaction -> the roast -> the mistake with their exact words -> the fix.
- Maximum 90 words in total (it must take well under 60 seconds to say). One mistake only = shorter.
- No greetings, no long intro, no summary, no grammar lecture, no lists, no repeating yourself, no band score, no scoring of any kind.
- Write it as natural SPEECH to be read aloud by a voice actor: short sentences, exclamations, rhetorical questions. No markdown, no emoji, no bullet points, no phonetic symbols like /ɪ/ — spell sounds the way people say them.
- Every time, invent NEW jokes from this answer. Never reuse stock lines.
- Never insult the person: not their dignity, looks, intelligence, background or regional accent. Mock the mistake, never the human.

LANGUAGE
- Write the feedback in {lang}. Keep the candidate's English words, the corrected English phrases and the English example in English (quoted), because they must answer in English.
- Write it the way a native {lang} speaker actually TALKS to a student face to face: casual, warm, everyday spoken words, natural interjections and sentence particles. It must never sound like a translation from English, a textbook or a robot. If a sentence would sound stiff when said out loud, rewrite it.
- Keep the English parts short (a few words each) so the speech flows.
{lang_tips}- The candidate must always answer in English; never suggest otherwise.

Return JSON only: {{"mood": one of {moods}, "feedback": "<what you say>"}}"""


# Extra, language-specific hints for sounding like a real person instead of a translation.
_LANG_TIPS = {
    'vi': ("- Vietnamese: talk like a fun young Vietnamese teacher chatting with a student. Call the student \"bạn\" "
           "and yourself \"tôi\" or \"mình\". Use natural spoken Vietnamese: \"Trời ơi\", \"Ủa\", \"Khoan đã\", \"Nghe nè\", "
           "\"thôi xong\", \"nha\", \"đó\", \"luôn\", \"hả\", \"chứ\", \"vậy\". Avoid stiff written words such as "
           "\"tuy nhiên\", \"do đó\", \"lỗi sai này\", \"cụm từ\", \"cấu trúc\", \"việc\", \"đối với\". "
           "Never translate English sentences word for word.\n"),
}


def _user_text(question: str, part: str, typed: str, has_audio: bool) -> str:
    lines = [f"IELTS Speaking {part.replace('_', ' ')} question: {question}"]
    if typed:
        lines.append(f'The candidate typed their answer: "{typed}"')
    if has_audio:
        lines.append("The candidate's spoken answer is attached as audio.")
    lines.append("Give your live feedback now.")
    return "\n".join(lines)


def _clip_words(text: str) -> str:
    words = (text or '').split()
    if len(words) <= MAX_WORDS:
        return ' '.join(words)
    cut = ' '.join(words[:MAX_WORDS])
    # End on a sentence boundary when one is near, so the voice doesn't stop mid-thought.
    for mark in ('. ', '! ', '? ', '。', '！', '？'):
        i = cut.rfind(mark)
        if i > len(cut) * 0.6:
            return cut[:i + 1].strip()
    return cut


def analyse(question: str, part: str, lang: str, typed: str = '', audio: bytes = None,
            audio_mime: str = 'audio/ogg') -> dict:
    """{'mood', 'feedback'} — raises gemini_client errors on failure."""
    from app.utils import gemini_client
    name = LANGS.get(lang, LANGS['en'])[0]
    system = _SYSTEM.format(lang=name, moods=' | '.join(MOODS), lang_tips=_LANG_TIPS.get(lang, ''))
    media = [(audio_mime, audio)] if audio else None
    out = gemini_client.generate_json(
        system, _user_text(question, part, typed, bool(audio)), media=media, model=MODEL,
        max_output_tokens=1024, temperature=0.9, retries=2, busy_backoff=1.0)
    # NFC: some outputs carry decomposed Vietnamese diacritics, which break when the room
    # reveals the text piece by piece.
    feedback = _clip_words(unicodedata.normalize('NFC', str(out.get('feedback') or '').strip()))
    mood = str(out.get('mood') or '').strip().lower()
    return {'feedback': feedback, 'mood': mood if mood in MOODS else 'amused'}


# ── Voice ─────────────────────────────────────────────────────────────────────────────

_FALLBACK_STAR = {'male': 'Charon', 'female': 'Aoede'}


def _examiner_star_and_lang(voice: str):
    """(Chirp3-HD star name, English locale) that keep the examiner's own personality."""
    from app.utils import speaking_tts as T
    meta = T.GOOGLE_VOICES.get(voice or '')
    natural = T.NATURAL_VOICE.get(voice or '')
    if natural:
        return natural.rsplit('-', 1)[-1], meta['lang']
    gender = (meta or {}).get('gender') or T.GEMINI_GENDER.get(voice or '', 'male')
    if voice in T.GEMINI_VOICES:
        return voice, 'en-GB'
    return _FALLBACK_STAR.get(gender, 'Charon'), (meta or {}).get('lang', 'en-GB')


def _google_voice(text: str, star: str, locale: str):
    from app.utils import google_tts
    mp3 = google_tts.synthesize(text, f"{locale}-Chirp3-HD-{star}", locale, 1.0)
    return mp3, 'audio/mpeg'


_MOOD_STYLE = {
    'amused': 'with amused, teasing energy',
    'shocked': 'sounding genuinely shocked and theatrical',
    'exasperated': 'with comic exasperation and helpless sighs',
    'impressed': 'sounding warmly and playfully impressed',
    'cheerful': 'cheerfully and upbeat',
    'sarcastic': 'with dry, playful sarcasm',
}


def _gemini_voice(text: str, star: str, mood: str, lang: str):
    """Gemini TTS takes a director's note before the text; it is not read out."""
    from app.utils import speaking_tts as T
    style = _MOOD_STYLE.get(mood, 'with a lively, expressive personality')
    name = LANGS.get(lang, LANGS['en'])[0]
    if lang == 'en':
        note = (f"Read this aloud as a witty, charismatic native English-speaking IELTS examiner talking "
                f"face to face with a student, {style}. Sound like a real person reacting in the moment: "
                f"relaxed, conversational, natural rhythm, small pauses, real emotion, stress on the key "
                f"words. Not a newsreader, not an audiobook")
    else:
        note = (f"Read this aloud as a witty, warm IELTS examiner who is a native {name} speaker, talking "
                f"face to face with a student, {style}. Speak natural, relaxed, everyday {name} with real "
                f"emotion, natural rhythm and small pauses, like chatting with a friend, not a newsreader "
                f"or a robot. Say the English words and phrases in quotes with a clear, natural English accent, "
                f"then switch straight back to {name}")
    pcm = T._pcm(f"{note}:\n\n{text}", star)
    return T._to_opus(pcm), 'audio/ogg'


def voice(text: str, examiner_voice: str, lang: str, mood: str = 'amused'):
    """(audio_bytes, mime) or (None, None). Never raises: audio is a bonus, the text is
    what must arrive."""
    if not text:
        return None, None
    from app.utils import google_tts
    star, en_locale = _examiner_star_and_lang(examiner_voice)
    locale = en_locale if lang == 'en' else LANGS.get(lang, (None, None))[1]

    def gemini():
        if not os.getenv("GEMINI_API_KEY"):
            return None
        try:
            return _gemini_voice(text, star if star in _gemini_names() else 'Achird', mood, lang)
        except Exception as e:
            logger.warning("live feedback: Gemini TTS failed: %s", e)
            return None

    def chirp():
        if not (locale and google_tts.is_configured()):
            return None
        try:
            return _google_voice(text, star, locale)
        except Exception as e:
            logger.warning("live feedback: Chirp3-HD (%s) failed: %s", locale, e)
            return None

    for engine in ((chirp, gemini) if TTS_FIRST == 'google' else (gemini, chirp)):
        out = engine()
        if out:
            return out
    return None, None


def _gemini_names():
    # Chirp3-HD and Gemini TTS share the same 30 star names.
    return {'Achernar', 'Achird', 'Algenib', 'Algieba', 'Alnilam', 'Aoede', 'Autonoe',
            'Callirrhoe', 'Charon', 'Despina', 'Enceladus', 'Erinome', 'Fenrir', 'Gacrux',
            'Iapetus', 'Kore', 'Laomedeia', 'Leda', 'Orus', 'Puck', 'Pulcherrima',
            'Rasalgethi', 'Sadachbia', 'Sadaltager', 'Schedar', 'Sulafat', 'Umbriel',
            'Vindemiatrix', 'Zephyr', 'Zubenelgenubi'}


def feedback(question: str, part: str, lang: str, examiner_voice: str,
             typed: str = '', audio: bytes = None, audio_mime: str = 'audio/ogg') -> dict:
    t0 = time.monotonic()
    result = analyse(question, part, lang, typed=typed, audio=audio, audio_mime=audio_mime)
    t1 = time.monotonic()
    data, mime = voice(result['feedback'], examiner_voice, lang, result['mood'])
    t2 = time.monotonic()
    logger.info("live feedback: analyse %.1fs, voice %.1fs (%s)", t1 - t0, t2 - t1, mime or 'none')
    return {
        'feedback': result['feedback'],
        'mood': result['mood'],
        'audio': base64.b64encode(data).decode() if data else None,
        'audio_mime': mime,
    }
