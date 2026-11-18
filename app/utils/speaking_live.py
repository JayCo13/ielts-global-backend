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
import json
import queue
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


# Vietnamese: ONE fixed regional accent per examiner gender, never a mix
# (2026-11-18: "nam thì giọng nam, nữ thì giọng bắc").
_VI_DIALECT = {
    'male': {
        'voice': ("Speak Vietnamese with a natural SOUTHERN (Saigon) accent all the way through, "
                  "never switching to a Northern or Central accent"),
        'words': ("- Vietnamese dialect: you are a man from Saigon. Use SOUTHERN spoken words only, e.g. "
                  "\"nè\", \"nha\", \"hông\", \"dzậy\"/\"vậy\", \"hả\", \"trời đất\", \"má ơi\", "
                  "\"thiệt\", \"cái gì dzậy trời\", \"bộ\". Never use Northern-only words such as "
                  "\"thế\", \"nhé\", \"ạ\", \"ơ kìa\", \"chứ lị\".\n"),
    },
    'female': {
        'voice': ("Speak Vietnamese with a natural NORTHERN (Hà Nội) accent all the way through, "
                  "never switching to a Southern or Central accent"),
        'words': ("- Vietnamese dialect: you are a woman from Hà Nội. Use NORTHERN spoken words only, e.g. "
                  "\"nhé\", \"thế\", \"thế à\", \"ơ kìa\", \"trời ạ\", \"chứ\", \"đấy\", \"cơ\", "
                  "\"gì thế này\", \"thật đấy\". Never use Southern-only words such as "
                  "\"nè\", \"hông\", \"dzậy\", \"thiệt\", \"má ơi\".\n"),
    },
}


def _lang_tips(lang: str, gender=None) -> str:
    tips = _LANG_TIPS.get(lang, '')
    if lang == 'vi' and gender in _VI_DIALECT:
        tips += _VI_DIALECT[gender]['words']
    return tips


def _accent(lang: str, gender: str) -> str:
    return _VI_DIALECT[gender]['voice'] + ". " if lang == 'vi' and gender in _VI_DIALECT else ''


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
            audio_mime: str = 'audio/ogg', recent=None, gender=None) -> dict:
    """{'mood', 'feedback'} — raises gemini_client errors on failure."""
    from app.utils import gemini_client
    name = LANGS.get(lang, LANGS['en'])[0]
    system = _SYSTEM.format(lang=name, moods=' | '.join(MOODS), lang_tips=_lang_tips(lang, gender))
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
    pcm = T._pcm(_gemini_prompt(text, star, mood, lang), star)
    return T._to_opus(pcm), 'audio/ogg'


def _gemini_prompt(text: str, star: str, mood: str, lang: str) -> str:
    gender = 'female' if star in _FEMALE_STARS else 'male'
    persona = _persona(gender)
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
                f"the punchline. {_accent(lang, gender)}Say the English words in quotes with a clear, natural "
                f"English accent, then switch straight back to {name}")
    # ONE call for the whole text. Splitting it in two halves was faster but broke the
    # intonation at the join and used two requests of the Gemini TTS daily quota per
    # feedback, so the quota ran out and everything fell back to Chirp (2026-11-17).
    return f"{note}:\n\n{text}"


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


# ── Streaming voice (2026-11-18, "feedback nhanh hơn") ────────────────────────────────
# The examiner starts talking as soon as Gemini has produced the first piece of audio,
# instead of after the whole clip is synthesised, encoded and sent in one go. Same single
# request, same voice and note as voice(); only the delivery changes.
STREAM_FIRST_AUDIO_BUDGET = float(os.getenv("SPEAKING_LIVE_STREAM_BUDGET", "8"))
STREAM_RATE = 24000     # Gemini TTS PCM: s16le mono 24 kHz


def _gemini_stream(prompt: str, star: str, out: "queue.Queue"):
    """Push ('pcm', base64) items, then ('end', None) or ('error', exc). Runs in a thread."""
    import requests
    from app.utils import speaking_tts as T
    try:
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": star}}},
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{T._MODEL}:streamGenerateContent"
        with requests.post(url, params={"key": T._api_key(), "alt": "sse"}, json=body,
                           stream=True, timeout=(4, 60)) as res:
            if res.status_code == 429:
                raise T.TtsQuota("Gemini TTS quota exhausted")
            if res.status_code != 200:
                raise T.TtsError(f"Gemini TTS stream returned {res.status_code}: {res.text[:200]}")
            for line in res.iter_lines():
                if not line or not line.startswith(b"data:"):
                    continue
                obj = json.loads(line[5:])
                for cand in obj.get("candidates") or []:
                    for part in (cand.get("content") or {}).get("parts") or []:
                        data = (part.get("inlineData") or {}).get("data")
                        if data:
                            out.put(('pcm', data))
        out.put(('end', None))
    except Exception as e:
        out.put(('error', e))


def voice_stream(text: str, examiner_voice: str, lang: str, mood: str = 'sarcastic'):
    """Yield dicts for the room: {'type':'pcm','data':b64,'rate'} … then one of
    {'type':'full','audio','audio_mime'} (Chirp fallback, whole clip) and finally
    {'type':'done','engine'}. Never raises."""
    from app.utils import google_tts
    t0 = time.monotonic()
    star, _ = _live_star(examiner_voice)
    _, en_locale = _examiner_star_and_lang(examiner_voice)
    locale = en_locale if lang == 'en' else LANGS.get(lang, (None, None))[1]

    def chirp():
        if not (text and locale and google_tts.is_configured()):
            return None
        try:
            return _google_voice(text, star, locale)
        except Exception as e:
            logger.warning("live stream: Chirp3-HD (%s) failed: %s", locale, e)
            return None

    got = 0
    if text and os.getenv("GEMINI_API_KEY"):
        q = queue.Queue()
        _pool.submit(_gemini_stream, _gemini_prompt(text, star, mood, lang), star, q)
        # Chirp is made in parallel only as insurance; it is used only when Gemini has not
        # produced any audio within the budget.
        c = _pool.submit(chirp)
        deadline = t0 + STREAM_FIRST_AUDIO_BUDGET
        while True:
            try:
                wait = max(0.05, deadline - time.monotonic()) if not got else 30
                kind, val = q.get(timeout=wait)
            except queue.Empty:
                if not got:
                    logger.warning("live stream: no Gemini audio after %ss, using Chirp3-HD",
                                   STREAM_FIRST_AUDIO_BUDGET)
                break
            if kind == 'pcm':
                if not got:
                    logger.info("live stream: first audio after %.1fs", time.monotonic() - t0)
                got += 1
                yield {'type': 'pcm', 'data': val, 'rate': STREAM_RATE}
            elif kind == 'error':
                logger.warning("live stream: Gemini TTS failed (%s): %s", type(val).__name__, val)
                break
            else:
                break
        if got:
            logger.info("live stream: done in %.1fs, %d chunks", time.monotonic() - t0, got)
            yield {'type': 'done', 'engine': 'gemini-stream'}
            return
        try:
            out = c.result(timeout=10)
        except Exception:
            out = None
    else:
        out = chirp()
    if out:
        data, mime = out
        yield {'type': 'full', 'audio': base64.b64encode(data).decode(), 'audio_mime': mime}
        yield {'type': 'done', 'engine': 'google-chirp'}
    else:
        yield {'type': 'done', 'engine': 'none'}


# ── Realtime examiner: Gemini Live (2026-11-18, "realtime đồng bộ như examiner trên mạng") ─
# One Live API session per answer: the model HEARS the answer and SPEAKS the feedback
# itself (native audio), streaming audio + its own transcript. No separate analysis call
# and no separate TTS call, so the examiner starts talking ~1-2 s after the answer, and the
# text on screen is exactly what is being said, revealed in step with the voice.
# If the Live session fails before any audio, the old analyse + TTS path takes over.
REALTIME = (os.getenv("SPEAKING_LIVE_REALTIME") or "1").strip() not in ("0", "false", "off")
REALTIME_MODEL = (os.getenv("SPEAKING_LIVE_REALTIME_MODEL") or "gemini-3.8-live").strip()
REALTIME_FIRST_AUDIO_BUDGET = float(os.getenv("SPEAKING_LIVE_REALTIME_BUDGET", "10"))
_LIVE_WS = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")


class _NoLiveAudio(Exception):
    """The Live session produced no audio; nothing was sent to the room yet."""


def _to_pcm16k(raw: bytes):
    import subprocess
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except Exception as e:
        raise _NoLiveAudio(f"ffmpeg: {e}")
    if proc.returncode != 0 or not proc.stdout:
        raise _NoLiveAudio("ffmpeg could not decode the answer")
    return proc.stdout


def _realtime_system(question, part, lang, gender, typed, has_audio, recent):
    name = LANGS.get(lang, LANGS['en'])[0]
    base = _SYSTEM.format(lang=name, moods=' | '.join(MOODS), lang_tips=_lang_tips(lang, gender))
    base = base.split("\nMOOD:", 1)[0].rstrip()
    voice = (f"{_persona(gender)}. You are speaking out loud, live, face to face: a lively, quick, "
             f"expressive delivery with real emotion, stressing the key words. ")
    if lang != 'en':
        voice += (f"Speak everyday spoken {name}. {_accent(lang, gender)}Say the quoted English words with a "
                  f"clear, natural English accent, then switch straight back to {name}. ")
    context = _user_text(question, part, typed, has_audio, recent).replace(
        "The candidate's spoken answer is attached as audio.",
        "The candidate's spoken answer comes next as audio.")
    return (f"{base}\n\nVOICE\n- {voice}\n- Say ONLY your feedback, out loud, then stop. Never read "
            f"out JSON, labels or instructions.\n\nTHIS ANSWER\n{context}")


def _realtime(question, part, lang, star, gender, typed, pcm, recent):
    """Yield room events from one Gemini Live session. Raises _NoLiveAudio only before the
    first event; after that, problems just end the stream."""
    from websockets.sync.client import connect
    key = os.getenv("GEMINI_API_KEY")
    t0 = time.monotonic()
    try:
        ws = connect(f"{_LIVE_WS}?key={key}", open_timeout=5, close_timeout=1, max_size=None)
    except Exception as e:
        raise _NoLiveAudio(f"connect: {type(e).__name__}")
    with ws:
        try:
            ws.send(json.dumps({"setup": {
                "model": f"models/{REALTIME_MODEL}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": star}}},
                },
                "systemInstruction": {"parts": [{"text": _realtime_system(
                    question, part, lang, gender, typed, bool(pcm), recent)}]},
                "outputAudioTranscription": {},
                # We send the whole answer at once: no voice-activity guessing, the turn
                # ends exactly at activityEnd.
                "realtimeInputConfig": {"automaticActivityDetection": {"disabled": True}},
            }}))
            hello = json.loads(ws.recv(timeout=8))
            if 'setupComplete' not in hello:
                raise _NoLiveAudio(f"setup: {str(hello)[:200]}")
            if pcm:
                ws.send(json.dumps({"realtimeInput": {"activityStart": {}}}))
                step = 32000     # 1 s of 16 kHz s16le
                for i in range(0, len(pcm), step):
                    ws.send(json.dumps({"realtimeInput": {"audio": {
                        "data": base64.b64encode(pcm[i:i + step]).decode(),
                        "mimeType": "audio/pcm;rate=16000"}}}))
                ws.send(json.dumps({"realtimeInput": {"activityEnd": {}}}))
            else:
                ws.send(json.dumps({"clientContent": {"turns": [{"role": "user", "parts": [
                    {"text": f'My answer (typed): "{typed}"'}]}], "turnComplete": True}}))
        except _NoLiveAudio:
            raise
        except Exception as e:
            raise _NoLiveAudio(f"send: {type(e).__name__}: {e}")

        started = False
        pending, said = [], []
        deadline = t0 + REALTIME_FIRST_AUDIO_BUDGET
        while True:
            try:
                # After the voice has started, 5 s of silence means the turn is over even if
                # turnComplete never arrives — don't hold the room's stream open.
                raw = ws.recv(timeout=max(0.1, deadline - time.monotonic()) if not started else 5)
            except Exception as e:
                if not started:
                    raise _NoLiveAudio(f"recv: {type(e).__name__}")
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            sc = msg.get("serverContent") or {}
            for p in (sc.get("modelTurn") or {}).get("parts") or []:
                data = (p.get("inlineData") or {}).get("data")
                if not data:
                    continue
                if not started:
                    started = True
                    # warning level: the server runs with --log-level warning.
                    logger.warning("live realtime: first audio after %.1fs (voice %s, %s)", time.monotonic() - t0,
                                   star, REALTIME_MODEL)
                    yield {'type': 'start', 'mood': 'sarcastic', 'voice_gender': gender, 'voice': star,
                           'engine': f'gemini-live ({REALTIME_MODEL})'}
                    for t in pending:
                        yield {'type': 'say', 'text': t}
                    pending = []
                yield {'type': 'pcm', 'data': data, 'rate': STREAM_RATE}
            tr = (sc.get("outputTranscription") or {}).get("text")
            if tr:
                said.append(tr)
                if started:
                    yield {'type': 'say', 'text': tr}
                else:
                    pending.append(tr)
            if sc.get("turnComplete") or sc.get("interrupted") or msg.get("goAway"):
                break
        if not started:
            raise _NoLiveAudio("no audio in the reply")
        text = unicodedata.normalize('NFC', ''.join(said)).strip()
        logger.warning("live realtime: done in %.1fs", time.monotonic() - t0)
        yield {'type': 'done', 'engine': 'gemini-live', 'feedback': text}


def feedback_events(question, part, lang, examiner_voice, typed='', raw=b'', raw_mime='audio/webm',
                    recent=None):
    """Events for /feedback/stream, produced in a background thread.

    The work (Live websocket, ffmpeg, Gemini calls) never runs inside this generator itself:
    when the room stops listening, Starlette drops the generator and Python may close it on
    the EVENT LOOP thread — closing a websocket there blocked the whole server for seconds
    (2026-11-18: 2nd answer failed, /subscription/status timed out). Here closing only sets
    a flag; the worker thread finishes and cleans up on its own."""
    q = queue.Queue()
    stop = threading.Event()

    def run():
        try:
            for ev in _feedback_events(question, part, lang, examiner_voice, typed, raw, raw_mime, recent):
                if stop.is_set():
                    break
                q.put(('ev', ev))
            q.put(('end', None))
        except BaseException as e:      # handed to the consumer, which raises it
            q.put(('err', e))

    threading.Thread(target=run, name="live-feedback", daemon=True).start()
    try:
        while True:
            kind, val = q.get(timeout=90)
            if kind == 'ev':
                yield val
            elif kind == 'err':
                raise val
            else:
                return
    finally:
        stop.set()


def _feedback_events(question, part, lang, examiner_voice, typed='', raw=b'', raw_mime='audio/webm',
                     recent=None):
    """Events for /feedback/stream. The FIRST event only comes once the voice is ready, so
    text and voice reach the room together. Raises (before any event) only when even the
    fallback analysis fails."""
    star, gender = _live_star(examiner_voice)
    if REALTIME and os.getenv("GEMINI_API_KEY"):
        try:
            pcm = _to_pcm16k(raw) if raw else b''
            yield from _realtime(question, part, lang, star, gender, typed, pcm, recent)
            return
        except _NoLiveAudio as e:
            logger.warning("live realtime: falling back to analyse + TTS (%s)", e)
    from app.utils import speaking_audio
    clip, mime = None, 'audio/ogg'
    if raw:
        clip, ext = speaking_audio.to_opus(raw)
        if not ext:
            mime = raw_mime
    result = analyse(question, part, lang, typed=typed, audio=clip, audio_mime=mime, recent=recent,
                     gender=gender)
    head = {'type': 'text', 'feedback': result['feedback'], 'mood': result['mood'], 'voice_gender': gender,
            'voice': star}
    sent = False
    for item in voice_stream(result['feedback'], examiner_voice, lang, result['mood']):
        if not sent:
            yield head          # together with the first audio, never before it
            sent = True
        yield item


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
    result = analyse(question, part, lang, typed=typed, audio=audio, audio_mime=audio_mime, recent=recent,
                     gender=_live_star(examiner_voice)[1])
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
