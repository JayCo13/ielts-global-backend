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
import random
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
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
# Used when the main model fails (busy, quota, blocked): the student still gets feedback.
FALLBACK_MODEL = "gemini-flash-lite-latest"
# Seconds to wait for Gemini TTS before using the Chirp3-HD voice made in parallel. The room
# gives up after 30s in total, so analysis + voice must stay well inside that.
# 12s: shorter than this pushed most answers onto the flatter Chirp voice (2026-11-17,
# "giọng không còn hay"). The room waits 30s in total.
# TWO fixed feedback voices, picked by the gender of the examiner the student chose
# (2026-11-17: "examiner nữ thì giọng feedback nữ và ngược lại, nam đổi sang Fenrir").
# Gemini/Chirp3-HD star names; the same name exists in both engines, so the Chirp fallback
# keeps the same speaker too. SPEAKING_LIVE_VOICE is the older single-voice setting and
# still overrides the male voice if set.
LIVE_VOICE_MALE = (os.getenv("SPEAKING_LIVE_VOICE_MALE") or os.getenv("SPEAKING_LIVE_VOICE") or "Fenrir").strip()
LIVE_VOICE_FEMALE = (os.getenv("SPEAKING_LIVE_VOICE_FEMALE") or "Kore").strip()
LIVE_VOICE = LIVE_VOICE_MALE
_FEMALE_STARS = {'Achernar', 'Aoede', 'Autonoe', 'Callirrhoe', 'Despina', 'Erinome', 'Gacrux', 'Kore',
                 'Laomedeia', 'Leda', 'Pulcherrima', 'Sulafat', 'Vindemiatrix', 'Zephyr'}
LIVE_GENDER = 'female' if LIVE_VOICE in _FEMALE_STARS else 'male'


def _persona(gender: str) -> str:
    """Fixed character sheet sent with every request so each clip sounds like the same person."""
    return ("Voice: always the same person, a confident " +
            ('woman in her' if gender == 'female' else 'man in his') +
            " mid-thirties with a warm, clear, "
            "medium-pitched voice and steady volume. Keep this exact voice, accent and microphone distance "
            "from start to finish; only the tone changes, never the voice itself")


_PERSONA = _persona(LIVE_GENDER)
GEMINI_TTS_BUDGET = float(os.getenv("SPEAKING_LIVE_TTS_BUDGET", "12"))
# Chirp speaking rate: a bit quicker than normal so the roast lands ("đọc nhanh lên cho catchy").
CHIRP_RATE = float(os.getenv("SPEAKING_LIVE_CHIRP_RATE", "1.05"))
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="live-tts")
DAILY_LIMIT = int(os.getenv("SPEAKING_LIVE_DAILY_LIMIT", "150"))
# Hard cap; the prompt asks for ≤ 40. Shorter text = shorter TTS = examiner talks sooner
# (2026-11-17 "tốc độ trả lời còn chậm").
MAX_WORDS = 60

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

MOODS = ('furious', 'helpless', 'shocked', 'sarcastic', 'amused', 'impressed')


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

_SYSTEM = """You are a REAL IELTS Speaking examiner in "Live Action" mode, reacting out loud right after a candidate answers one question. Your character: brutally sarcastic, hilariously expressive, emotionally exaggerated, and genuinely helpful. Picky, sharp-tongued, impatient, theatrical. You are NOT a polite, gentle, neutral IELTS teacher, and you never sound like an AI reading a report.

WHAT TO PICK
- Listen to the answer (audio, and/or the typed text). Consider Fluency and Coherence, Lexical Resource, Grammatical Range and Accuracy, Pronunciation, but never go through all four.
- Pick only the 1-2 MOST noticeable mistakes: ones that clearly hurt the answer or the band, that the learner can easily notice and remember, and that one concrete example fixes. Prefer basic mistakes, repeated mistakes, obvious pronunciation slips and unnatural phrasing. Ignore small slips.
- Pronunciation: only a sound you clearly HEAR wrong in the audio (e.g. "play" said as "lay", "go" said like "gao"). Never invent a mistake the candidate did not make; with no audio, say nothing about pronunciation.

HOW TO REACT: SARCASM IS THE DEFAULT
- When there is a noticeable mistake, sarcasm and roasting are the MAIN style, not a garnish. React like a real examiner who is annoyed, helpless, or cannot believe what they just heard, as if about to lose it. Do not just state "you made a mistake".
- Be blunt, harsh and cutting. You MAY use casual swearing and rough, informal language when it fits (e.g. "What the hell was that?", "Trời đất ơi", "quái quỷ gì thế này"). No slurs, nothing sexual.
- Use exaggeration, mock disbelief, rhetorical questions, wordplay, funny comparisons and unexpected images, all tied DIRECTLY to what they actually said. Quote their exact words.
- Scale it to the mistake: small slip = light teasing or skip it; clear mistake = direct, sharp sarcasm; basic or serious mistake = roast hard, over-the-top shock or despair; the same mistake repeated = visibly out of patience. Never soften a basic mistake into polite advice.
- After the roast you MUST still name the mistake and give the exact correct version (or the right pronunciation) they can use immediately.
- Good answer: no forced joke. Praise it in an exaggerated, excited, surprised way with personality, never a flat "good job".
- Almost nothing said, or off-topic: roast THAT, then say what a real answer needs.
- Mock the mistake, never the human: nothing about their dignity, looks, real intelligence, personal background or regional accent.

ORDER (flexible, not every part needed): 1) roast / emotional reaction, 2) the mistake with their own words, 3) the fix, 4) optionally one short, unexpected closing jab.

SHORT, SHARP, SARCASTIC & MEMORABLE
- Maximum 40 words in total (about 15 seconds spoken). One mistake only = even shorter. Every word must earn its place: cut warm-up, cut repetition.
- No greetings, no intro, no summary, no grammar lecture, no lists, no repeating an explanation, no band score or scoring of any kind. No hollow comfort like "good attempt" or "well done" when the answer was full of mistakes.
- At least one memorable roast line in every answer that has a noticeable mistake.
- Invent NEW jokes from THIS answer every time, built from its own words and topic. Never fall back on stock lines or the same opening exclamation; a real comedian never tells the same joke twice to the same student.
- Wide, vivid vocabulary: vary your exclamations, verbs, comparisons and sentence shapes. Do not start two feedbacks the same way.
- Write it as SPEECH for a voice actor: short punchy sentences, exclamations, rhetorical questions, dramatic pauses written as "..." . No markdown, no emoji, no bullet points, no phonetic symbols like /ɪ/; spell sounds the way people say them.

LANGUAGE
- Write the feedback in {lang}. Keep the candidate's English words, the corrected English phrases and the English example in English (quoted), because they must answer in English. Short English exclamations are fine too.
- Write it the way a native {lang} speaker actually TALKS when roasting a friend: casual, everyday spoken words, natural interjections and particles. It must never sound like a translation from English, a textbook or a robot.
- Keep the English parts short (a few words each) so the speech flows.
{lang_tips}- The candidate must always answer in English; never suggest otherwise.

MOOD: pick the one that matches how you deliver it: furious (annoyed, about to explode), helpless (despair, giving up), shocked (cannot believe it), sarcastic (dry mockery), amused (teasing, laughing at it), impressed (over-the-top excited praise).

Return JSON only: {{"mood": one of {moods}, "feedback": "<what you say>"}}"""


# Extra, language-specific hints for sounding like a real person instead of a translation.
_LANG_TIPS = {
    'vi': ("- Vietnamese: talk like a sharp-tongued, dramatic Vietnamese examiner roasting a student. Call the student \"bạn\" "
           "and yourself \"tôi\". Use natural spoken Vietnamese: \"Trời ơi\", \"Trời đất ơi\", \"Ủa\", \"Khoan đã!\", \"hả trời\", "
           "\"thôi xong\", \"nha\", \"đó\", \"luôn\", \"hả\", \"chứ\", \"vậy\", \"dữ vậy\", \"Má ơi\", \"Ơ kìa\", "
           "\"Hả, gì cơ?\", \"Thôi chết\", \"Nghe mà muốn xỉu\", \"Xin bạn đó\", \"Đỉnh thật sự\", \"Gì kỳ vậy\", "
           "\"Nói thật nha\", \"Tôi chịu luôn\", \"Cạn lời\". These are only examples: vary them, use each rarely, "
           "and often open with no interjection at all. Avoid stiff written words such as "
           "\"tuy nhiên\", \"do đó\", \"lỗi sai này\", \"cụm từ\", \"cấu trúc\", \"việc\", \"đối với\". "
           "Never translate English sentences word for word.\n"),
}


# One comic device and one opening shape are drawn at random for every answer, so a
# whole test doesn't sound like the same joke on repeat (2026-11-17 "feedback trùng nhiều").
_ANGLES = (
    "an absurd, oddly specific comparison from everyday life (food, traffic, pets, a phone battery, a broken printer...)",
    "a mini dramatic scene, as if the mistake were a crime, a disaster movie or a sports commentary",
    "fake-serious official tone, like announcing breaking news or reading a court verdict about the mistake",
    "a rhetorical question that turns their own words against them",
    "personifying the misused word or grammar as a victim that is suffering or complaining",
    "pretending to misunderstand them literally, showing how funny the wrong word sounds",
    "mock self-pity: you, the examiner, suffering and needing a break after hearing it",
    "a quick countdown or exaggerated number joke about how many times it happened",
    "sarcastic fake praise that flips into the real problem",
    "a playful 'imagine if' scenario of a native speaker hearing this sentence",
)
_OPENINGS = (
    "Open by quoting their exact words back at them.",
    "Open with a short rhetorical question.",
    "Open with a dramatic one-word or two-word reaction you have not used before.",
    "Open straight into the joke, no exclamation.",
    "Open with a fake calm, deadpan statement.",
)


def _user_text(question: str, part: str, typed: str, has_audio: bool, recent=None) -> str:
    lines = [f"IELTS Speaking {part.replace('_', ' ')} question: {question}"]
    if typed:
        lines.append(f'The candidate typed their answer: "{typed}"')
    if has_audio:
        lines.append("The candidate's spoken answer is attached as audio.")
    lines.append(f"Style for THIS feedback if there is a mistake to roast: use {random.choice(_ANGLES)}. "
                 f"{random.choice(_OPENINGS)}")
    recent = [r for r in (recent or []) if r][-4:]
    if recent:
        lines.append("You already said these to this candidate earlier in the test. Do NOT reuse their "
                     "openings, exclamations, jokes, images or phrasing; sound fresh:")
        lines += [f'- "{r[:300]}"' for r in recent]
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
            audio_mime: str = 'audio/ogg', recent=None) -> dict:
    """{'mood', 'feedback'} — raises gemini_client errors on failure."""
    from app.utils import gemini_client
    name = LANGS.get(lang, LANGS['en'])[0]
    system = _SYSTEM.format(lang=name, moods=' | '.join(MOODS), lang_tips=_LANG_TIPS.get(lang, ''))
    media = [(audio_mime, audio)] if audio else None
    user = _user_text(question, part, typed, bool(audio), recent)
    try:
        out = gemini_client.generate_json(
            system, user, media=media, model=MODEL, max_output_tokens=1024, temperature=1.05,
            retries=1, busy_backoff=1.0, safety_off=True)
        if not str(out.get('feedback') or '').strip():
            raise gemini_client.GeminiError("empty feedback")
    except gemini_client.GeminiNotConfigured:
        raise
    except Exception as e:
        if (MODEL or '') == FALLBACK_MODEL:
            raise
        logger.warning("live feedback: %s failed (%s), retrying on %s", MODEL, e, FALLBACK_MODEL)
        out = gemini_client.generate_json(
            system, user, media=media, model=FALLBACK_MODEL, max_output_tokens=1024,
            temperature=1.05, retries=2, busy_backoff=1.0, safety_off=True)
    # NFC: some outputs carry decomposed Vietnamese diacritics, which break when the room
    # reveals the text piece by piece.
    feedback = _clip_words(unicodedata.normalize('NFC', str(out.get('feedback') or '').strip()))
    mood = str(out.get('mood') or '').strip().lower()
    return {'feedback': feedback, 'mood': mood if mood in MOODS else 'sarcastic'}


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


def _examiner_gender(voice: str) -> str:
    from app.utils import speaking_tts as T
    meta = T.GOOGLE_VOICES.get(voice or '') or {}
    return meta.get('gender') or T.GEMINI_GENDER.get(voice or '', 'male')


def _live_star(examiner_voice: str):
    """(star, gender) of the fixed feedback voice matching the examiner's gender."""
    gender = 'female' if _examiner_gender(examiner_voice) == 'female' else 'male'
    star = LIVE_VOICE_FEMALE if gender == 'female' else LIVE_VOICE_MALE
    if star not in _gemini_names():
        star = 'Kore' if gender == 'female' else 'Fenrir'
    return star, gender


def _google_voice(text: str, star: str, locale: str):
    from app.utils import google_tts
    mp3 = google_tts.synthesize(text, f"{locale}-Chirp3-HD-{star}", locale, CHIRP_RATE)
    return mp3, 'audio/mpeg'


# Tone only. Timbre words (gasp, sigh, whisper, cheering, "big pitch jump") made Gemini
# TTS change the voice itself between answers (2026-11-17 "giọng không stable").
_MOOD_STYLE = {
    'furious': 'irritated and sharp, like you are losing patience',
    'helpless': 'in mock despair, like you cannot believe it',
    'shocked': 'in theatrical disbelief',
    'sarcastic': 'with dry, mocking sarcasm',
    'amused': 'teasing and amused',
    'impressed': 'genuinely excited and impressed',
}


def _gemini_voice(text: str, star: str, mood: str, lang: str):
    """Gemini TTS takes a director's note before the text; it is not read out."""
    from app.utils import speaking_tts as T
    persona = _persona('female' if star in _FEMALE_STARS else 'male')
    tone = _MOOD_STYLE.get(mood, _MOOD_STYLE['sarcastic'])
    name = LANGS.get(lang, LANGS['en'])[0]
    if lang == 'en':
        note = (f"{persona}. Role: a sarcastic, expressive native English-speaking IELTS examiner roasting a "
                f"student face to face. Tone for this line: {tone}. Natural conversational rhythm, a brisk "
                f"lively pace, stress the key words, short pause before the punchline")
    else:
        note = (f"{persona}. Role: a sarcastic, expressive IELTS examiner who is a native {name} speaker, "
                f"roasting a student face to face in everyday spoken {name}. Tone for this line: {tone}. "
                f"Natural conversational rhythm, a brisk lively pace, stress the key words, short pause before "
                f"the punchline. Say the English words in quotes with a clear, natural English accent, then "
                f"switch straight back to {name}")
    # ONE call for the whole text. Splitting it in two halves was faster but broke the
    # intonation at the join and used two requests of the Gemini TTS daily quota per
    # feedback, so the quota ran out and everything fell back to Chirp (2026-11-17).
    pcm = T._pcm(f"{note}:\n\n{text}", star)
    return T._to_opus(pcm), 'audio/ogg'


def voice(text: str, examiner_voice: str, lang: str, mood: str = 'sarcastic'):
    """(audio_bytes, mime) or (None, None). Never raises: audio is a bonus, the text is
    what must arrive."""
    if not text:
        return None, None
    from app.utils import google_tts
    _, en_locale = _examiner_star_and_lang(examiner_voice)
    star, _ = _live_star(examiner_voice)
    locale = en_locale if lang == 'en' else LANGS.get(lang, (None, None))[1]

    def gemini():
        if not os.getenv("GEMINI_API_KEY"):
            return None
        try:
            return _gemini_voice(text, star, mood, lang)
        except Exception as e:
            # TtsQuota = the Gemini TTS daily cap is used up → Chirp until it resets.
            logger.warning("live feedback: Gemini TTS failed (%s): %s", type(e).__name__, e)
            return None

    def chirp():
        if not (locale and google_tts.is_configured()):
            return None
        try:
            return _google_voice(text, star, locale)
        except Exception as e:
            logger.warning("live feedback: Chirp3-HD (%s) failed: %s", locale, e)
            return None

    if TTS_FIRST == 'google':
        return chirp() or gemini() or (None, None)
    # Gemini TTS sounds far better but can be slow or out of quota: start Chirp at the same
    # time and use it if Gemini has not answered within the budget.
    g = _pool.submit(gemini)
    c = _pool.submit(chirp)
    try:
        out = g.result(timeout=GEMINI_TTS_BUDGET)
    except FutureTimeout:
        logger.warning("live feedback: Gemini TTS slower than %ss, using Chirp3-HD", GEMINI_TTS_BUDGET)
        out = None
    if out:
        return out
    try:
        return c.result(timeout=10) or (None, None)
    except Exception:
        return None, None


def _gemini_names():
    # Chirp3-HD and Gemini TTS share the same 30 star names.
    return {'Achernar', 'Achird', 'Algenib', 'Algieba', 'Alnilam', 'Aoede', 'Autonoe',
            'Callirrhoe', 'Charon', 'Despina', 'Enceladus', 'Erinome', 'Fenrir', 'Gacrux',
            'Iapetus', 'Kore', 'Laomedeia', 'Leda', 'Orus', 'Puck', 'Pulcherrima',
            'Rasalgethi', 'Sadachbia', 'Sadaltager', 'Schedar', 'Sulafat', 'Umbriel',
            'Vindemiatrix', 'Zephyr', 'Zubenelgenubi'}


def feedback(question: str, part: str, lang: str, examiner_voice: str,
             typed: str = '', audio: bytes = None, audio_mime: str = 'audio/ogg', recent=None) -> dict:
    t0 = time.monotonic()
    result = analyse(question, part, lang, typed=typed, audio=audio, audio_mime=audio_mime, recent=recent)
    t1 = time.monotonic()
    data, mime = voice(result['feedback'], examiner_voice, lang, result['mood'])
    _, gender = _live_star(examiner_voice)
    t2 = time.monotonic()
    engine = {'audio/ogg': 'gemini', 'audio/mpeg': 'google-chirp'}.get(mime, 'none')
    logger.info("live feedback: analyse %.1fs, voice %.1fs, engine=%s", t1 - t0, t2 - t1, engine)
    return {
        'feedback': result['feedback'],
        'mood': result['mood'],
        'audio': base64.b64encode(data).decode() if data else None,
        'audio_mime': mime,
        'engine': engine,
        'voice_gender': gender,
    }
