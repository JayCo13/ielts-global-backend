"""Ba chức năng CÓ gọi AI của trang phân tích (docs/speaking-spec.md §6.4–§6.5).

§6.8 là luật chi phối cả file: chỉ chạy khi học viên **chủ động bấm nút**, không bao giờ
chạy vì họ mở trang. Mỗi hàm ở đây tương ứng đúng một nút.

Target band luôn được chọn TRƯỚC khi gọi — spec nói thẳng lý do là tiết kiệm token: sinh
sẵn cả bốn mức rồi để học viên chọn sau là trả tiền cho ba mức không ai đọc.

Global port: every explanation, note, tip and verdict the model writes is English (VN asks
for Vietnamese); the student-facing gate messages below are English too.
"""
import json
import logging
import re
from difflib import SequenceMatcher

from app.utils import gemini_client

logger = logging.getLogger(__name__)

# Model đọc chữ dùng bản nhẹ; chấm phát âm thì bắt buộc bản nghe được (giống §5).
TEXT_MODEL = None                       # None = mặc định flash-lite của gemini_client
AUDIO_MODEL = "gemini-flash-latest"

# Bốn mức mục tiêu của bản feedback 23/09. '4.5-5.5' là tên cũ của mức thấp nhất, giữ lại
# để giao diện cũ (hoặc tab đang mở) gửi lên vẫn hiểu được thay vì âm thầm rơi về 6.0-6.5.
BANDS = ('5.0-5.5', '6.0-6.5', '7.0-7.5', '8.0-9.0')
_BAND_ALIASES = {'4.5-5.5': '5.0-5.5'}


def _band_or_default(target_band: str) -> str:
    band = _BAND_ALIASES.get(target_band, target_band)
    return band if band in BANDS else '6.0-6.5'


# ── §6.4 ✨ Cải thiện câu trả lời ───────────────────────────────────────────────────────

_IMPROVE_SYSTEM = """You rewrite an IELTS Speaking answer to a target band, in English.

Hard rules — breaking any of them makes the output useless to the student:
1. KEEP THE STUDENT'S OWN CONTENT. Same opinion, same examples, same facts about their
   life. You are improving HOW it is said, never WHAT is said.
2. INVENT NOTHING PERSONAL. No new jobs, cities, family members, hobbies or events. If
   the answer is thin, develop the idea already there instead of adding a new one.
3. Do not change their stance, even to a more defensible one.
4. Sound like a real candidate speaking, not written prose: contractions, natural
   discourse markers, no bullet points, no headings.
5. Match the target band honestly. A 4.5-5.5 rewrite must still contain simple
   structures; do not hand back band 8 language and label it band 5.

Return ONLY this JSON:
{"improved": "the rewritten answer",
 "changes": [{"from": "exact phrase from the original", "to": "the replacement",
              "why": "one short sentence, in simple English"}],
 "note": "one or two sentences in simple English on what to practise next"}

"changes" must quote the original EXACTLY so the interface can highlight it; list the 3-6
changes that matter most, not every word you touched."""


def improve_answer(question: str, answer: str, target_band: str) -> dict:
    band = _band_or_default(target_band)
    user = (f"Question: {question}\n\n"
            f"The student's own answer:\n\"\"\"\n{answer.strip()}\n\"\"\"\n\n"
            f"Target band: {band}")
    return gemini_client.generate_json(_IMPROVE_SYSTEM, user, model=TEXT_MODEL,
                                       temperature=0.4, max_output_tokens=2048)


# ── §6.4 💡 Tạo câu trả lời từ ý tưởng ─────────────────────────────────────────────────

_IDEAS_SYSTEM = """You turn a student's rough ideas into a full IELTS Speaking answer.

The ideas may be in the student's own language, in broken English, or a mix — that is normal and is not
something to comment on. The student is telling you WHAT they want to say; your job is to
say it well in English at the target band.

Hard rules:
1. STAY ON THEIR IDEAS. Every point in the answer must trace back to something they wrote.
   Do not add a second reason, a new example, or a personal detail they did not give.
2. If their ideas are too thin for a full answer, expand what is there — explain it,
   give consequences, add a concrete detail that follows from it — rather than inventing
   a different topic.
3. Sound spoken, not written. Match the target band honestly.

Return ONLY this JSON:
{"answer": "the answer in English",
 "used_ideas": ["each idea of theirs you used, quoted or closely paraphrased"],
 "note": "one or two sentences in simple English, e.g. which idea was too thin to use"}"""


def answer_from_ideas(question: str, ideas: str, target_band: str) -> dict:
    band = _band_or_default(target_band)
    user = (f"Question: {question}\n\n"
            f"The student's ideas:\n\"\"\"\n{ideas.strip()}\n\"\"\"\n\n"
            f"Target band: {band}")
    return gemini_client.generate_json(_IDEAS_SYSTEM, user, model=TEXT_MODEL,
                                       temperature=0.5, max_output_tokens=2048)


# ── Cửa kiểm "có người nói đúng câu đó không" — chạy TRƯỚC mọi lần chấm phát âm ────────
#
# Lỗi phát hiện 19/09: gửi im lặng tuyệt đối, tiếng ồn trắng hay một tiếng bíp 220 Hz cho
# từ "green" đều được 95-96/100; bíp cho bài shadowing được 85. Học viên bấm ghi âm rồi
# ngồi im cũng ăn điểm gần tuyệt đối — cả tính năng mất nghĩa.
#
# Vì sao sửa prompt chấm là không đủ: prompt ĐÃ CÓ câu "nếu không phải câu mục tiêu thì trả
# null", và model vẫn làm đúng khi ai đó đọc SAI TỪ ("yellow" thay "green" → nó từ chối).
# Nó chỉ hỏng khi KHÔNG có giọng người: được đưa sẵn câu mục tiêu kèm bản ghi, nó "nghe
# thấy" đúng thứ nó được bảo là sẽ nghe. Nên tách việc NGHE khỏi việc BIẾT ĐÁP ÁN:
#   1. `signal_problem` — ffmpeg đo: im lặng, âm đơn (bíp), âm thanh đều đều (tiếng ồn).
#      Bắt được thì dừng luôn, không gọi AI, không tốn lượt.
#   2. `speech_problem` — một lượt nghe MÙ: model không được biết câu mục tiêu, chỉ nói
#      trong bản ghi có gì và chép lại lời. Code tự so với câu mục tiêu.
#   3. Qua cả hai mới gọi `score_pronunciation` / `score_shadowing` như cũ.

NO_SOUND = ("No sound was detected in the recording. Check that your microphone is on and "
            "allowed in the browser, then read loudly and clearly and try again.")
# Câu này cũng hiện cho người ĐÃ đọc nhưng quá nhỏ và rè (thử 19/09: từ "of" qua mic kém,
# nhỏ đi 30 dB bị nhận là không có giọng). Nên nó phải chỉ cách khắc phục, chứ không chỉ
# kết luận "không có giọng người" như thể học viên không làm gì.
NO_SPEECH = ("We could not hear a voice reading in the recording — only {what}. "
             "If you did read, move the microphone closer, speak loudly and clearly, and try again.")
MISMATCH = ("The recording does not match the practice text (we heard: \"{heard}\"). Please "
            "read \"{target}\" exactly and try again.")
# Name kept from VN (call sites use it); values are English in the global port.
_CONTENT_VI = {'silence': 'silence', 'tone': 'a flat tone like a beep',
               'noise': 'background noise', 'music': 'music', 'other': 'sounds that are not speech'}

_LISTEN_SYSTEM = """You are an audio checker. You are NOT told what the person was supposed to
say, and you must not guess it. Report only what is actually in the recording.

- "speech": true ONLY if a human voice saying words is clearly audible. Pure tones, beeps,
  whistles, electronic sounds, music, humming without words, hiss, static, white noise,
  clicks, breathing, coughing and silence are NOT speech -> false.
- "content": one of "speech", "silence", "tone", "noise", "music", "other".
- "transcript": the English words you actually hear, verbatim, in lowercase, "" if none.
  Never invent or complete words you did not hear. If a word is mispronounced, write what
  it sounded like, not what it was probably meant to be.

Return ONLY JSON: {"speech": true, "content": "speech", "transcript": "..."}"""


def signal_problem(raw: bytes, debug_tag: str = None):
    """Câu báo lỗi nếu bản ghi rõ ràng KHÔNG có người nói — im lặng, âm đơn (bíp), hay âm
    thanh đều đều (tiếng ồn nền) — None nếu ổn HOẶC không đo được. Chỉ dùng ffmpeg, không
    gọi AI, nên chặn ở đây thì không trừ lượt. Đo được gì thì chắc chắn cái đó; không chắc
    thì để lượt nghe AI quyết định, chứ không chặn oan."""
    from app.utils import speaking_audio
    result = _signal_problem(raw)
    # TẠM (xem GATE_DEBUG_DIR): ghi số đo mọi lượt, lưu bản ghi của tài khoản test.
    try:
        logger.warning("SPEAKING_GATE %s -> %s | %s", debug_tag or '-',
                       'BLOCK' if result else 'pass', speaking_audio.gate_metrics(raw))
        if debug_tag:
            speaking_audio.keep_for_debug(raw, f"{'CHAN' if result else 'QUA'}_{debug_tag}")
    except Exception as e:      # chẩn đoán không bao giờ được làm hỏng lượt luyện
        logger.warning("SPEAKING_GATE logging failed: %s", e)
    return result


def _signal_problem(raw: bytes):
    from app.utils import speaking_audio
    peak = speaking_audio.peak_db(raw)
    if peak is not None and peak < speaking_audio.SILENT_PEAK_DB:
        return NO_SOUND
    # Mức DUY TRÌ, không phải đỉnh: bản ghi im lặng vẫn có thể có đỉnh cao vì một tiếng
    # click lúc bấm nút (xem SPEECH_LEVEL_DB).
    level = speaking_audio.sustained_db(raw)
    if level is not None and level < speaking_audio.SPEECH_LEVEL_DB:
        return NO_SOUND
    if speaking_audio.looks_like_tone(raw):
        return NO_SPEECH.format(what=_CONTENT_VI['tone'])
    if speaking_audio.is_steady(raw):
        return NO_SPEECH.format(what=_CONTENT_VI['noise'])
    return None


def _words(text: str) -> list:
    return re.findall(r"[a-z]+(?:'[a-z]+)?", (text or '').lower())


def heard_matches(target: str, heard: str) -> bool:
    """Lời nghe được có phải là người đó đang cố đọc câu mục tiêu không.

    Cố ý DỄ TÍNH: đây không phải chấm điểm, chỉ là loại những bản ghi rõ ràng không liên
    quan. Người đọc sai âm thì lời chép ra cũng sai theo ("think" → "sink", "green" →
    "grin") — những bản đó phải ĐI QUA để được chấm và chỉ ra lỗi, chứ không bị chặn.
    Một từ mục tiêu coi là "nghe thấy" khi có từ nào giống nó từ 60% chữ cái trở lên;
    cần nghe thấy ít nhất một nửa số từ (câu một từ thì phải nghe thấy từ đó).
    """
    want, got = _words(target), _words(heard)
    if not want:
        return True
    if not got:
        return False
    hits = sum(1 for w in want
               if max(SequenceMatcher(None, w, g).ratio() for g in got) >= 0.6)
    return hits >= max(1, (len(want) + 1) // 2)


def speech_problem(target: str, audio: bytes, mime: str = 'audio/ogg'):
    """Câu báo lỗi nếu bản ghi không có người đọc câu mục tiêu, None nếu ổn.

    Lượt nghe này hỏng (mạng, AI bận) thì CHO QUA để chấm như trước — chặn oan học viên vì
    lỗi của mình còn tệ hơn để lọt một bản ghi.
    """
    # MỘT TỪ đứng riêng: bỏ hẳn lượt nghe AI. Thử 19/09, với từ lẻ nó thất thường theo cả
    # hai chiều — giọng đọc "bad" rõ ràng bị chép thành "a woman", "set" bị cho là "tiếng
    # ồn", "of" nói nhỏ bị chặn 3/3 — tức là chặn oan học viên, mà bài học phát âm lại toàn
    # từ lẻ. Những gì nó từng bắt được thì giờ đã có chỗ khác bắt chắc hơn: im lặng, bíp,
    # tiếng ồn đều → `signal_problem` (ffmpeg, không may rủi); đọc SAI TỪ → chính bộ chấm tự
    # trả null ("yellow" thay "green"). Còn lọt: nhạc, tiếng gõ phím với từ lẻ — hiếm, và
    # hại ít hơn nhiều so với chặn oan. Bỏ được thêm một lần gọi AI cho mỗi lượt luyện từ.
    # Câu nhiều từ thì bản ghi dài, chép lời ổn định, nên vẫn nghe và so khớp bên dưới.
    if len(_words(target)) <= 1:
        return None
    try:
        d = gemini_client.generate_json(_LISTEN_SYSTEM, "The recording is attached.",
                                        media=[(mime, audio)], model=AUDIO_MODEL,
                                        temperature=0.0, max_output_tokens=512)
    except Exception as e:
        logger.warning("Recording check (blind listen) failed, letting it through: %s", e)
        return None
    heard = ' '.join(str(d.get('transcript') or '').split())
    content = str(d.get('content') or '').lower()
    if d.get('speech') is False or (not heard and content != 'speech'):
        return NO_SPEECH.format(what=_CONTENT_VI.get(content, 'sounds that are not speech'))
    if not heard_matches(target, heard):
        short = lambda t: t if len(t) <= 80 else t[:77] + '...'
        return MISMATCH.format(heard=short(heard), target=short(' '.join(target.split())))
    return None


# ── §6.5 🗣️ Chấm phát âm ───────────────────────────────────────────────────────────────

_PRON_SYSTEM = """You assess the pronunciation of one short recording against the text the
student was trying to say.

Judge TWO things, always, and weigh them together:
  1. INDIVIDUAL SOUNDS — vowels and consonants said wrongly, dropped endings, added sounds.
  2. WORD STRESS — which syllable carries the stress in every word of two or more
     syllables. This is not optional and not a footnote: an English word said with the
     wrong stressed syllable is frequently misheard even when every sound in it is
     correct, and learners whose first language is syllable-timed or tonal routinely
     flatten stress. Check every multi-syllable word in the target text. For a one-syllable target
     there is no word stress to judge — say so in "word_stress_verdict" and leave the list
     empty.

Score 0-100, reflecting BOTH sounds and word stress. This scale is for pronunciation
practice ONLY — it is NOT an IELTS band and must never be described as one. Anchor it:
90+ = a listener would notice nothing; 75-89 = clearly intelligible with a few slips;
60-74 = understandable but effortful; below 60 = the listener has to guess. Consistently
misplaced stress must pull the score down even if the individual sounds are clean.

Judge only what you can actually hear. If the recording is too quiet, too short, or the
words are not the target text, say so in "problem" and return a null score rather than
guessing — a made-up number teaches the student the wrong thing.

Return ONLY this JSON:
{"score": 82,
 "problem": null,
 "sounds": [{"sound": "/θ/", "heard_as": "/t/", "in_words": ["think", "three"],
             "how": "one short sentence in simple English on how to fix it"}],
 "word_stress_verdict": "one sentence in simple English: was the stress right overall?",
 "word_stress": [{"word": "comfortable", "said": "com-FOR-ta-ble",
                  "correct": "COM-fort-a-ble", "ok": false,
                  "how": "one short sentence in simple English on how to fix it"}],
 "clarity": "one sentence in simple English on overall clarity, pace and volume",
 "tips": ["one or two short drills in simple English"]}

In "word_stress", write the syllables separated by hyphens with the stressed one in CAPS,
both for what you heard ("said") and for the correct form ("correct"); set "ok" to true
when the student got that word right. List every multi-syllable word you could hear
clearly, right ones included — the student needs to know what they already do well.
Elsewhere, leave a list empty rather than filling it with something you did not hear."""


def score_pronunciation(target_text: str, audio: bytes, mime: str = 'audio/ogg') -> dict:
    """Chấm một bản ghi ngắn so với câu học viên định đọc.

    Bắt buộc dùng model nghe được: không có audio thì không có gì để chấm, và đoán từ
    transcript là chấm nhầm thứ khác.
    """
    user = (f"The student was trying to say:\n\"\"\"\n{target_text.strip()}\n\"\"\"\n\n"
            "Their recording is attached.")
    return gemini_client.generate_json(_PRON_SYSTEM, user, media=[(mime, audio)],
                                       model=AUDIO_MODEL, temperature=0.2,
                                       max_output_tokens=2048)


# ── AI Shadowing (feedback 06/09) ──────────────────────────────────────────────────────
#
# Nghe mẫu → nhắc lại → nhận xét. Khác "chấm phát âm" ở §6.5 về mục đích: chỗ kia chấm một
# từ hay một cụm cho đúng âm, chỗ này soi cả đoạn theo năm chiều mà người Việt hay vướng
# nhất khi nói dài — trong đó ba chiều (trọng âm câu, ngữ điệu, nối âm) chỉ lộ ra khi nói
# cả câu, không thể thấy ở mức từ lẻ.
#
# Giới hạn 1 lượt/ngày là yêu cầu của user, và cũng hợp lý: mỗi lượt gửi cả đoạn audio cho
# model nghe được, đắt hơn hẳn mấy chức năng đọc chữ.

_SHADOW_SYSTEM = """You are a shadowing coach. The student heard a natural model reading of
a passage, then shadowed it — repeating it while imitating the model's delivery. You have
their recording and the target text.

Judge how closely they matched the MODEL'S DELIVERY, not merely whether the words are
right. Two recordings with identical transcripts can be a band apart: what separates them
is prosody. So listen to the audio itself — rhythm, pausing, pitch movement, linking,
loudness — and never build the feedback from the transcript alone.

Report on exactly these six dimensions, in this order:
  pronunciation     — individual sounds or words said wrongly
  word_stress       — stress on the wrong syllable of a word
  sentence_stress   — the wrong words emphasised within the sentence
  intonation        — pitch movement that sounds unnatural for the meaning
  connected_speech  — linking and weak forms: a natural speaker runs words together and
                      reduces function words
  rhythm_fluency    — pausing and pace: where they broke the flow, hesitations, whether
                      the phrasing followed sense groups or chopped words apart

For each dimension give a short verdict in simple English and at most three concrete examples
quoting the student's own words. If a dimension was fine, say so briefly rather than
inventing a fault — a coach who always finds six problems teaches nothing.

Judge only what you can hear. If the recording is too quiet, too short, or clearly not the
target passage, put that in "problem" and return null for "score".

Score 0-100 for overall closeness to the model. This is a shadowing practice score, NOT an
IELTS band, and must never be presented as one.

Return ONLY this JSON:
{"score": 78,
 "problem": null,
 "dimensions": [
   {"key": "pronunciation", "verdict": "one sentence",
    "examples": [{"said": "...", "should_be": "...", "note": "..."}]},
   {"key": "word_stress", "verdict": "...", "examples": []},
   {"key": "sentence_stress", "verdict": "...", "examples": []},
   {"key": "intonation", "verdict": "...", "examples": []},
   {"key": "connected_speech", "verdict": "...", "examples": []},
   {"key": "rhythm_fluency", "verdict": "...", "examples": []}
 ],
 "summary": "two or three sentences in simple English: what to practise next"}"""

SHADOW_DIMENSIONS = ('pronunciation', 'word_stress', 'sentence_stress',
                     'intonation', 'connected_speech', 'rhythm_fluency')


def score_shadowing(target_text: str, audio: bytes, mime: str = 'audio/ogg') -> dict:
    user = (f"The passage they were shadowing:\n\"\"\"\n{target_text.strip()}\n\"\"\"\n\n"
            "Their recording is attached.")
    return gemini_client.generate_json(_SHADOW_SYSTEM, user, media=[(mime, audio)],
                                       model=AUDIO_MODEL, temperature=0.2,
                                       max_output_tokens=3072)


# ── Pronunciation Lessons (feedback 09/09) ─────────────────────────────────────────────
#
# AI đọc phần LÝ THUYẾT admin viết rồi tự ra bộ luyện tập. Vì sao để AI làm chứ không bắt
# admin nhập tay: mỗi Unit cần một bộ mới mỗi lần học viên bấm "Làm mới", mà nội dung phải
# bám đúng âm đang dạy — nhập tay thì đến Unit thứ ba là hết kiên nhẫn.
#
# Từ → luyện phát âm (chấm âm và trọng âm); câu → shadowing (nhịp, nối âm, ngữ điệu). Đó
# là phân công trong feedback, và cũng là chỗ mỗi cơ chế làm tốt nhất.

LESSON_BATCH = 10          # "AI tự tạo Practice → 10 từ/câu dựa trên nội dung lý thuyết"

_LESSON_SYSTEM = """You build the practice set for one pronunciation lesson.

You are given the lesson's TITLE and the teacher's THEORY notes (about English
pronunciation; usually in English, occasionally in another language). Produce items the student will read aloud to practise
exactly what that lesson teaches.

Hard rules:
1. STAY INSIDE THE LESSON. Every item must exercise the specific sounds, stress pattern
   or feature the theory is about. An item that does not contain the target feature is
   worthless here, however good an English sentence it is.
2. Mix the two kinds deliberately:
   - "word"     — a single English word, for drilling the sound or the stress itself.
   - "sentence" — one natural spoken English sentence (8-16 words) that contains the
                  target feature several times, for shadowing.
   Roughly two thirds words and one third sentences unless the lesson is clearly about
   connected speech, rhythm or intonation — those can only be practised in sentences, so
   give mostly sentences then.
3. Order them easy → hard.
4. Use real, common English. No invented words, no tongue-twisters that no one would say.
5. "note" is one short line in simple English telling the student what to watch in THAT item
   (which sound, which syllable carries the stress, where to link). Never leave it empty.

Return ONLY this JSON:
{"items": [{"kind": "word", "content": "sheep", "note": "long /iː/ — hold it longer than in 'ship'"},
           {"kind": "sentence", "content": "...", "note": "..."}]}"""


def lesson_items(title: str, theory: str, count: int = LESSON_BATCH,
                 avoid=None) -> dict:
    """Sinh một bộ từ/câu luyện tập cho một Unit.

    `avoid` là những nội dung đã ra ở bộ trước — truyền vào để nút "Làm mới" thật sự cho
    bài khác, chứ không trả lại gần như y hệt bộ cũ.
    """
    seen = [str(x) for x in (avoid or []) if x][:60]
    user = (f"Lesson title: {title}\n\n"
            f"Teacher's theory notes:\n\"\"\"\n{(theory or '').strip()}\n\"\"\"\n\n"
            f"Produce exactly {count} practice items.")
    if seen:
        user += ("\n\nThe student has already practised these; give DIFFERENT ones:\n"
                 + "\n".join(f"- {x}" for x in seen))
    return gemini_client.generate_json(_LESSON_SYSTEM, user, model=TEXT_MODEL,
                                       temperature=0.7, max_output_tokens=3072)


# ── Soạn bài mẫu (feedback 23/09) ──────────────────────────────────────────────────────
# Khác §6.4 ở nguồn chữ: §6.4 viết lại CÂU HỌC VIÊN ĐÃ NÓI, còn ở đây học viên tự gõ một
# bài mẫu trước khi luyện nói. Hai hàm dưới đây nhận cả trường hợp chỉ bôi đen MỘT ĐOẠN —
# khi đó model chỉ được trả về đúng đoạn ấy, phần ghép lại do máy chủ làm bằng chỉ số ký
# tự. Để model tự trả nguyên bài thì nó sẽ lặng lẽ sửa cả những câu học viên không chọn.

_SELECTION_RULE = (
    "You are given the FULL draft for context and, between the markers «« »», the ONE "
    "excerpt the student selected. Rewrite ONLY that excerpt. Your output must be a drop-in "
    "replacement for it: do not repeat the surrounding text, do not add a leading or "
    "trailing sentence, and keep the same role in the sentence so the result still reads "
    "grammatically when spliced back in."
)

_DRAFT_IMPROVE_SYSTEM = """You improve an IELTS Speaking answer that a student wrote themselves, in English.

Hard rules — breaking any of them makes the output useless to the student:
1. KEEP THE STUDENT'S OWN CONTENT. Same opinion, same examples, same facts about their
   life. You improve HOW it is said, never WHAT is said.
2. INVENT NOTHING PERSONAL. No new jobs, cities, family members, hobbies or events.
3. Do not change their stance, even to a more defensible one.
4. It must sound like someone SPEAKING: contractions, natural discourse markers, no
   bullet points, no headings, no essay phrasing.
5. Match the target band honestly. A 5.0-5.5 rewrite must still contain simple
   structures; do not hand back band 8 language and label it band 5.

Return ONLY this JSON:
{"improved": "the rewritten text",
 "changes": [{"from": "exact phrase from the original", "to": "the replacement",
              "why": "one short sentence, in simple English"}],
 "note": "one or two sentences in simple English on what to practise next"}

"changes" must quote the original EXACTLY so the interface can highlight it; list the 3-6
changes that matter most, not every word you touched."""

_FIX_SYSTEM = """You are a proofreader for an IELTS Speaking answer a student wrote themselves.

Fix ONLY language errors — vocabulary and grammar:
- wrong word choice, wrong collocation, wrong preposition, wrong word form;
- tense, agreement, articles, plurals, word order.

Hard rules:
1. KEEP THE STUDENT'S MEANING AND IDEAS EXACTLY. You are not rewriting and not improving
   the band — a correct sentence stays as it is even if you could say it more elegantly.
2. Change nothing that is already correct, even if it is simple or plain.
3. Add no new content, no new examples, no linking phrases the student did not write.
4. Keep it spoken English, not written prose.

Return ONLY this JSON:
{"fixed": "the corrected text",
 "fixes": [{"from": "exact phrase from the original", "to": "the correction",
            "type": "vocabulary" or "grammar",
            "why": "one short sentence, in simple English"}],
 "note": "one short sentence in simple English naming the error the student repeats most"}

If the text has no real error, return it unchanged with an empty "fixes" list and say so in
"note". Never invent an error to look useful."""


def _marked(full: str, start, end):
    """(chữ gửi cho model, đoạn được chọn) — None nghĩa là sửa cả bài."""
    if start is None or end is None or start >= end:
        return full, None
    start = max(0, min(int(start), len(full)))
    end = max(start, min(int(end), len(full)))
    selection = full[start:end]
    if not selection.strip():
        return full, None
    return f"{full[:start]}««{selection}»»{full[end:]}", selection


def _splice(full: str, start, end, piece: str) -> str:
    """Ghép đoạn đã sửa vào đúng chỗ cũ. Không dùng replace() vì đoạn được chọn có thể
    xuất hiện nhiều lần trong bài."""
    if start is None or end is None or start >= end:
        return piece
    start = max(0, min(int(start), len(full)))
    end = max(start, min(int(end), len(full)))
    return full[:start] + piece + full[end:]


def improve_draft(question: str, full_text: str, start, end, target_band: str) -> dict:
    """Nâng bài học viên tự soạn lên band mục tiêu; bôi đen thì chỉ nâng đoạn đó."""
    band = _band_or_default(target_band)
    shown, selection = _marked(full_text, start, end)
    system = _DRAFT_IMPROVE_SYSTEM + ("\n\n" + _SELECTION_RULE if selection else "")
    user = (f"Question: {question}\n\n"
            f"The student's own draft:\n\"\"\"\n{shown.strip()}\n\"\"\"\n\n"
            f"Target band: {band}")
    data = gemini_client.generate_json(system, user, model=TEXT_MODEL,
                                       temperature=0.4, max_output_tokens=2048)
    piece = (data.get('improved') or '').strip()
    return {
        "text": _splice(full_text, start, end, piece) if selection else piece,
        "improved": piece,
        "selection": selection,
        "changes": data.get('changes') or [],
        "note": data.get('note'),
        "target_band": band,
    }


def fix_language(question: str, full_text: str, start, end) -> dict:
    """Chỉ sửa lỗi từ vựng và ngữ pháp, giữ nguyên ý của học viên."""
    shown, selection = _marked(full_text, start, end)
    system = _FIX_SYSTEM + ("\n\n" + _SELECTION_RULE if selection else "")
    user = (f"Question: {question}\n\n"
            f"The student's own draft:\n\"\"\"\n{shown.strip()}\n\"\"\"")
    data = gemini_client.generate_json(system, user, model=TEXT_MODEL,
                                       temperature=0.2, max_output_tokens=2048)
    piece = (data.get('fixed') or '').strip()
    return {
        "text": _splice(full_text, start, end, piece) if selection else piece,
        "fixed": piece,
        "selection": selection,
        "fixes": data.get('fixes') or [],
        "note": data.get('note'),
    }
