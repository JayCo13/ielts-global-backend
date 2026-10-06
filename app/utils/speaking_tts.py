"""Examiner voice for the Speaking test — pre-generated, never spoken by the browser.

docs/speaking-spec.md decision #3. The browser's own SpeechSynthesis was rejected because
the voice it picks depends on the student's operating system: the same test would sound
different on every machine, and on some it would not speak at all. So every line the
examiner says — the fixed script and every question in the bank — is synthesised once at
authoring time and stored.

Gemini's TTS model is used because the key is already provisioned and works (AWS Polly
would be the other obvious choice, but the IAM user here is scoped to SES only).

The model returns raw 24 kHz mono PCM, which is ~48 KB per second — far too heavy to ship
to students. ffmpeg re-encodes it to Opus, the same codec the spec picked for student
recordings; a five-second question lands around 15 KB.

Global port — engines and env vars (all optional; the app boots with none of them set and
every TTS feature then fails with a clean "not configured" error instead of crashing):
  SPEAKING_TTS_ENGINE   gemini (default) | google | polly
  gemini → GEMINI_API_KEY
  google → GOOGLE_TTS_CREDENTIALS (path to the service-account JSON, or the JSON itself)
  polly  → AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, region POLLY_REGION (else SES_REGION,
           else ap-southeast-1)
Voice labels shown to students are English.
"""
import base64
import hashlib
import logging
import os
import re
import subprocess
import time

import requests

_MODEL = "gemini-2.5-flash-preview-tts"
_ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{_MODEL}:generateContent"
# Split, not one number. Synthesis itself is slow, but a *handshake* never is — and the
# route out of this datacentre to Google drops most connections (measured 2026-08-27:
# 80-100% loss abroad, 0% domestically). Under one flat 120s budget a whole batch would
# crawl, spending two minutes per dropped dial before even reaching the next clip.
_TIMEOUT = (4, 120)     # (connect, read)
_DIAL_DEADLINE = 30.0   # seconds of re-dialling one clip before giving up on it

# Gemini's PCM output format, fixed by the API.
_PCM_RATE = 24000
_PCM_WIDTH = 2      # bytes per sample, s16le
_OPUS_BITRATE = "24k"

# The voices offered as "Chọn giọng Examiner". Kept deliberately short: every extra voice
# multiplies the whole bank by one more synthesis run.
#
# Chosen on the one thing that can be measured rather than on how they sound: speaking
# pace. A real examiner reads at roughly 130-150 words per minute — fast enough to keep
# the room moving, slow enough that a weaker candidate still catches the question. The
# six candidates measured 128 to 163 wpm on the same sentence; these three sit in the
# band, and Achird landed in the middle of it.
# Giới tính của giọng Gemini không có trong tài liệu chính thức nhưng nghe là phân biệt
# được, và người dùng cần ít nhất vài giám khảo nữ (feedback 31/08 và 01/09).
#
# Đo lại tốc độ nói ngày 01/09 trên cùng một câu 30 từ: Achird 228, Despina 201,
# Aoede 206, Kore 217, Leda 221, Callirrhoe 221, Autonoe 235 từ/phút. Con số tuyệt đối
# KHÁC hẳn lần đo 25/08 (Achird khi đó 145) vì câu mẫu khác nhau — chỉ so tương đối trong
# cùng một lần đo mới có nghĩa. Hai giọng nữ được chọn là hai giọng chậm nhất, tức gần
# nhịp giám khảo nhất trong nhóm.
#
# Đây KHÔNG phải lời giải cho yêu cầu ba accent: Gemini không gắn accent vào giọng. Phần
# đó chờ Polly ở dưới.
GEMINI_VOICES = ('Achird', 'Despina', 'Aoede', 'Schedar', 'Iapetus')
GEMINI_GENDER = {'Achird': 'male', 'Despina': 'female', 'Aoede': 'female',
                 'Schedar': 'male', 'Iapetus': 'male'}
# Thứ tự trong GEMINI_VOICES chính là thứ tự ưu tiên sinh clip (speaking_tts_generate sắp
# theo chỉ số này): giọng mặc định trước để không câu nào câm, rồi tới giọng nữ vừa thêm,
# sau cùng mới tới hai giọng nam phụ. Với trần 100 clip/ngày thì thứ tự này quyết định
# người dùng phải chờ mấy ngày mới có giọng nữ đầy đủ.

# ── Bộ giọng AWS Polly ────────────────────────────────────────────────────────────────
#
# Người dùng yêu cầu đủ ba accent × hai giới (feedback 31/08). **Gemini không làm được
# việc này**: giọng của nó không gắn với accent hay giới tính cụ thể, và mỗi model TTS
# còn bị trần 100 request/ngày kể cả khi đã trả tiền — cả kho ~1000 câu × 6 giọng là bất
# khả thi. Polly có sẵn giọng theo accent + giới, không trần ngày, cả kho khoảng 1 USD.
#
# Khoá AWS đã có sẵn trong .env (dùng chung với SES) và boto3 đã cài; chỉ còn thiếu quyền
# `polly:SynthesizeSpeech` + `polly:DescribeVoices` cho IAM user `ses-broadcast-sender`.
# Cấp quyền xong thì đặt SPEAKING_TTS_ENGINE=polly là chạy, không phải sửa code.
#
# Riêng en-AU chưa có giọng nam neural nên Russell dùng engine standard.
POLLY_VOICES = {
    'Amy':     {'accent': 'british',    'gender': 'female', 'lang': 'en-GB', 'engine': 'neural'},
    'Brian':   {'accent': 'british',    'gender': 'male',   'lang': 'en-GB', 'engine': 'neural'},
    'Joanna':  {'accent': 'american',   'gender': 'female', 'lang': 'en-US', 'engine': 'neural'},
    'Matthew': {'accent': 'american',   'gender': 'male',   'lang': 'en-US', 'engine': 'neural'},
    'Olivia':  {'accent': 'australian', 'gender': 'female', 'lang': 'en-AU', 'engine': 'neural'},
    'Russell': {'accent': 'australian', 'gender': 'male',   'lang': 'en-AU', 'engine': 'standard'},
}

# ── Bộ giọng Google Cloud TTS ─────────────────────────────────────────────────────────
#
# Đây là engine dùng thật cho yêu cầu "ba accent × hai giới". Gemini không gắn accent vào
# giọng và bị trần 100 clip/ngày; AWS Polly thì tài khoản không truy cập được. Cloud TTS
# dùng chung tài khoản Google mà hệ thống đã trả tiền, có sẵn giọng theo accent + giới
# tính, và không có trần theo ngày.
#
# Khoá là mã Google (lưu vào cột SpeakingTts.voice, tối đa 32 ký tự); `name` là tên
# giám khảo hiển thị cho thí sinh.
#
# `rate` chỉnh RIÊNG từng giọng. Ở cùng một tốc độ, sáu giọng này đọc lệch nhau rất nhiều
# (đo 01/09 trên cùng câu 24 từ, rate 0.92: 142 · 160 · 152 · 158 · 159 · 177 từ/phút) —
# để nguyên thì giọng Úc nam đọc nhanh gần gấp rưỡi giọng Anh nữ, thí sinh chọn giọng nào
# là gặp bài thi khác hẳn nhau. Mỗi số dưới đây kéo giọng đó về khoảng 145 từ/phút, nhịp
# của giám khảo thật.
# Bốn accent × bốn giọng (2 nữ, 2 nam). Mỗi giọng có TÊN giám khảo thay vì mã kỹ thuật:
# "en-GB-Neural2-C" không nói lên gì với thí sinh, còn "Alice · Giọng Anh · Nữ" thì có.
# Giọng Ấn Độ có thật trên Google (en-IN Neural2 đủ cả nam lẫn nữ) nên đưa vào luôn —
# nhiều giám khảo IELTS ở châu Á nói accent này.
#
# `rate` chỉnh RIÊNG từng giọng: ở cùng một tốc độ chúng đọc lệch nhau rất nhiều, để
# nguyên thì chọn giọng nào là gặp bài thi khác hẳn nhau. Số dưới đây kéo mỗi giọng về
# cùng một nhịp. Đo ngày 06/09 (câu 23 từ, ffprobe trên file tạm): sáu giọng cũ đọc
# 191-210 từ/phút, nên mười giọng mới được kéo về đúng dải đó bằng
# `rate_mới = rate_cũ × 200 / wpm_đo_được`.
#
# LƯU Ý CHƯA GIẢI QUYẾT: cả 16 giọng đang đọc ~200 từ/phút, còn giám khảo IELTS thật đọc
# khoảng 145. Muốn về đúng nhịp thật thì nhân toàn bộ rate với 145/200 ≈ 0,73 rồi sinh lại
# clip. Không tự làm vì đó là đổi trải nghiệm của mọi bài thi, phải hỏi user.
GOOGLE_VOICES = {
    # ── Giọng Anh ──
    'en-GB-Neural2-C': {'accent': 'british', 'gender': 'female', 'lang': 'en-GB', 'name': 'Alice',  'rate': 0.94},
    'en-GB-Neural2-N': {'accent': 'british', 'gender': 'female', 'lang': 'en-GB', 'name': 'Emily',  'rate': 0.80},
    'en-GB-Neural2-B': {'accent': 'british', 'gender': 'male',   'lang': 'en-GB', 'name': 'Tony',   'rate': 0.83},
    'en-GB-Neural2-O': {'accent': 'british', 'gender': 'male',   'lang': 'en-GB', 'name': 'Oliver', 'rate': 0.75},
    # ── Giọng Mỹ ──
    'en-US-Neural2-F': {'accent': 'american', 'gender': 'female', 'lang': 'en-US', 'name': 'Sophia', 'rate': 0.88},
    'en-US-Neural2-G': {'accent': 'american', 'gender': 'female', 'lang': 'en-US', 'name': 'Grace',  'rate': 0.80},
    'en-US-Neural2-D': {'accent': 'american', 'gender': 'male',   'lang': 'en-US', 'name': 'Daniel', 'rate': 0.84},
    'en-US-Neural2-J': {'accent': 'american', 'gender': 'male',   'lang': 'en-US', 'name': 'Ethan',  'rate': 0.84},
    # ── Giọng Úc ──
    'en-AU-Neural2-C': {'accent': 'australian', 'gender': 'female', 'lang': 'en-AU', 'name': 'Chloe', 'rate': 0.84},
    'en-AU-Neural2-A': {'accent': 'australian', 'gender': 'female', 'lang': 'en-AU', 'name': 'Mia',   'rate': 0.92},
    'en-AU-Neural2-B': {'accent': 'australian', 'gender': 'male',   'lang': 'en-AU', 'name': 'Jack',  'rate': 0.75},
    'en-AU-Neural2-D': {'accent': 'australian', 'gender': 'male',   'lang': 'en-AU', 'name': 'Liam',  'rate': 0.77},
    # ── Giọng Ấn Độ ──
    'en-IN-Neural2-A': {'accent': 'indian', 'gender': 'female', 'lang': 'en-IN', 'name': 'Priya', 'rate': 0.93},
    'en-IN-Neural2-D': {'accent': 'indian', 'gender': 'female', 'lang': 'en-IN', 'name': 'Anika', 'rate': 1.00},
    'en-IN-Neural2-B': {'accent': 'indian', 'gender': 'male',   'lang': 'en-IN', 'name': 'Rahul', 'rate': 0.83},
    'en-IN-Neural2-C': {'accent': 'indian', 'gender': 'male',   'lang': 'en-IN', 'name': 'Arjun', 'rate': 0.91},
}
_GOOGLE_DEFAULT = 'en-GB-Neural2-B'

# ── Giọng đọc mẫu "người thật" (feedback 09/09: "cho AI đọc natural hơn 1 chút") ──
#
# Neural2 là giọng của PHÒNG THI: đều, rõ, đọc câu hỏi một mạch — đúng thứ cần cho đề bài,
# nhưng đem đi làm mẫu để bắt chước thì người học học đúng cái nhịp máy đó. Chirp3-HD là
# thế hệ giọng mới của Google, ngắt nghỉ và lên xuống giọng như người nói thật.
#
# Chỉ dùng cho phần ĐỌC MẪU gọi tại chỗ (shadowing, "Nghe mẫu" của luyện phát âm). Kho clip
# câu hỏi đã sinh sẵn vẫn là Neural2 — đổi giọng cả kho là sinh lại hàng nghìn file.
#
# Ràng buộc của Chirp3-HD: KHÔNG nhận SSML (gửi thẻ <speak> vào là lỗi), nên đường này đọc
# văn bản trần và chỉ chỉnh tốc độ. Đo 09/09 trên câu 25 từ: rate 1.00 → 185 từ/phút,
# 0.75 → 151 — lấy 0.75 vì mẫu để bắt chước thì phải theo kịp.
NATURAL_RATE = 0.75
# Mỗi giám khảo giữ một giọng tự nhiên RIÊNG: học viên chọn Alice thì bản mẫu cũng phải là
# một giọng nữ Anh cố định, không đổi mỗi lần bấm.
NATURAL_VOICE = {
    'en-GB-Neural2-C': 'en-GB-Chirp3-HD-Achernar',    # Alice
    'en-GB-Neural2-N': 'en-GB-Chirp3-HD-Aoede',       # Emily
    'en-GB-Neural2-B': 'en-GB-Chirp3-HD-Achird',      # Tony
    'en-GB-Neural2-O': 'en-GB-Chirp3-HD-Algenib',     # Oliver
    'en-US-Neural2-F': 'en-US-Chirp3-HD-Autonoe',     # Sophia
    'en-US-Neural2-G': 'en-US-Chirp3-HD-Callirrhoe',  # Grace
    'en-US-Neural2-D': 'en-US-Chirp3-HD-Algieba',     # Daniel
    'en-US-Neural2-J': 'en-US-Chirp3-HD-Alnilam',     # Ethan
    'en-AU-Neural2-C': 'en-AU-Chirp3-HD-Despina',     # Chloe
    'en-AU-Neural2-A': 'en-AU-Chirp3-HD-Erinome',     # Mia
    'en-AU-Neural2-B': 'en-AU-Chirp3-HD-Charon',      # Jack
    'en-AU-Neural2-D': 'en-AU-Chirp3-HD-Enceladus',   # Liam
    'en-IN-Neural2-A': 'en-IN-Chirp3-HD-Gacrux',      # Priya
    'en-IN-Neural2-D': 'en-IN-Chirp3-HD-Kore',        # Anika
    'en-IN-Neural2-B': 'en-IN-Chirp3-HD-Fenrir',      # Rahul
    'en-IN-Neural2-C': 'en-IN-Chirp3-HD-Iapetus',     # Arjun
}

ACCENT_LABEL = {'british': 'British', 'american': 'American',
                'australian': 'Australian', 'indian': 'Indian', 'neutral': 'Neutral'}
GENDER_LABEL = {'female': 'Female', 'male': 'Male', 'neutral': ''}

_GEMINI_DEFAULT = 'Achird'
_POLLY_DEFAULT = 'Brian'


def engine() -> str:
    """'gemini' (mặc định), 'google' hoặc 'polly'. Đổi bằng biến môi trường."""
    return (os.getenv('SPEAKING_TTS_ENGINE') or 'gemini').strip().lower()


def voice_names() -> tuple:
    """Danh sách giọng của engine đang bật."""
    e = engine()
    if e == 'polly':
        return tuple(POLLY_VOICES)
    if e == 'google':
        return tuple(GOOGLE_VOICES)
    return GEMINI_VOICES


def default_voice() -> str:
    e = engine()
    if e == 'polly':
        return _POLLY_DEFAULT
    if e == 'google':
        return _GOOGLE_DEFAULT
    return _GEMINI_DEFAULT


def voice_catalog() -> list:
    """[{name, accent, gender, ...}] — để giao diện xếp nhóm theo accent và giới tính."""
    if engine() == 'google':
        # `display` là thứ thí sinh đọc: "Alice · Nữ". Mã kỹ thuật vẫn trả về vì nó là
        # khoá lưu trong DB, nhưng giao diện không được hiện nó ra.
        return [{'name': n, 'accent': m['accent'], 'gender': m['gender'],
                 'display': m['name'],
                 'label': f"{m['name']} · {GENDER_LABEL[m['gender']]}",
                 'accent_label': ACCENT_LABEL[m['accent']],
                 'gender_label': GENDER_LABEL[m['gender']]}
                for n, m in GOOGLE_VOICES.items()]
    if engine() == 'polly':
        return [{'name': n, 'accent': m['accent'], 'gender': m['gender'],
                 'accent_label': ACCENT_LABEL[m['accent']],
                 'gender_label': GENDER_LABEL[m['gender']]}
                for n, m in POLLY_VOICES.items()]
    return [{'name': n, 'accent': 'neutral', 'gender': GEMINI_GENDER.get(n, 'neutral'),
             'accent_label': ACCENT_LABEL['neutral'],
             'gender_label': GENDER_LABEL.get(GEMINI_GENDER.get(n, 'neutral'), '')}
            for n in GEMINI_VOICES]


# Hai tên cũ giữ lại cho tương thích; nơi nào cần đúng engine đang bật thì gọi hàm ở trên.
VOICES = GEMINI_VOICES
DEFAULT_VOICE = _GEMINI_DEFAULT


logger = logging.getLogger(__name__)


class TtsNotConfigured(Exception):
    """GEMINI_API_KEY is not set."""


class TtsError(Exception):
    """Synthesis or encoding failed."""


class TtsQuota(TtsError):
    """Gemini refused with 429. On the free tier this is a DAILY cap, not a per-minute
    one — retrying in a few seconds does not help, and a batch that keeps trying just
    burns through every remaining line marking it failed."""


def text_fingerprint(text: str, voice: str) -> str:
    """Identifies exactly what was spoken, so edited wording regenerates its audio.

    Without this an admin could fix a typo in a question and students would keep hearing
    the old wording forever — the bug is silent, because the file is still there.
    """
    clean = ' '.join((text or '').split())
    return hashlib.sha256(f"{voice}\x00{clean}".encode('utf-8')).hexdigest()


def _api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise TtsNotConfigured("GEMINI_API_KEY is not set")
    return key


def _pcm(text: str, voice: str) -> bytes:
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    key = _api_key()
    deadline = time.monotonic() + _DIAL_DEADLINE
    tries = 0
    while True:
        tries += 1
        try:
            res = requests.post(_ENDPOINT, params={"key": key}, json=body, timeout=_TIMEOUT)
            break
        except requests.RequestException as exc:
            if time.monotonic() >= deadline:
                raise TtsError(f"could not reach Gemini TTS after {tries} attempts: {exc}") from exc
            time.sleep(min(0.4 * tries, 1.5))
    if res.status_code == 429:
        raise TtsQuota("Gemini TTS quota exhausted (a daily cap on the free tier)")
    if res.status_code != 200:
        raise TtsError(f"Gemini TTS returned {res.status_code}: {res.text[:200]}")
    try:
        part = res.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
        return base64.b64decode(part["data"])
    except (KeyError, IndexError, ValueError) as exc:
        raise TtsError(f"Gemini TTS returned an unexpected shape: {exc}") from exc


def _to_opus(pcm: bytes) -> bytes:
    """PCM -> Opus in an Ogg container, which every current browser plays."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "s16le", "-ar", str(_PCM_RATE), "-ac", "1", "-i", "pipe:0",
        "-c:a", "libopus", "-b:a", _OPUS_BITRATE, "-vbr", "on",
        "-f", "ogg", "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, input=pcm, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TtsError(f"ffmpeg could not run: {exc}") from exc
    if proc.returncode != 0 or not proc.stdout:
        raise TtsError(f"ffmpeg error: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


def duration_ms(pcm_len: int) -> int:
    return int(pcm_len / (_PCM_RATE * _PCM_WIDTH) * 1000)


def _polly(text: str, voice: str):
    """(ogg_opus_bytes, duration_ms) từ AWS Polly.

    Xin MP3 rồi chuyển sang Opus bằng cùng đường ffmpeg đang dùng cho Gemini, để mọi clip
    trong kho cùng một định dạng dù sinh bằng engine nào.
    """
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    meta = POLLY_VOICES[voice]
    # Vùng lấy theo POLLY_REGION, không có thì dùng chung vùng của SES. Tách riêng vì
    # không phải vùng nào cũng có đủ giọng neural — đổi vùng không phải sửa cấu hình SES.
    region = os.getenv('POLLY_REGION') or os.getenv('SES_REGION') or 'ap-southeast-1'
    client = boto3.client('polly', region_name=region)

    def _call(engine_name):
        resp = client.synthesize_speech(Text=text, VoiceId=voice, Engine=engine_name,
                                        LanguageCode=meta['lang'], OutputFormat='mp3')
        return resp['AudioStream'].read()

    try:
        try:
            mp3 = _call(meta['engine'])
        except ClientError as e:
            # Vùng này chưa có giọng neural thì đọc bằng giọng standard còn hơn không có
            # tiếng. Chỉ lùi đúng trường hợp đó, các lỗi khác vẫn báo ra ngoài.
            code = e.response.get('Error', {}).get('Code', '')
            if meta['engine'] == 'neural' and 'Engine' in code:
                logger.warning("Polly: %s has no neural voice in %s, using standard",
                               voice, region)
                mp3 = _call('standard')
            else:
                raise
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        if 'AccessDenied' in code:
            raise TtsNotConfigured(
                "The AWS account lacks polly:SynthesizeSpeech permission") from e
        if 'Throttl' in code or 'LimitExceeded' in code:
            raise TtsQuota(f"Polly throttled: {code}") from e
        raise TtsError(f"Polly error: {code or e}") from e
    except BotoCoreError as e:
        raise TtsError(f"Could not reach Polly: {e}") from e

    from app.utils.speaking_audio import to_opus
    ogg, ext = to_opus(mp3)
    if not ext:
        raise TtsError("ffmpeg could not convert Polly MP3 to Opus")
    # Polly không trả độ dài; ước lượng theo cỡ file Opus 16kbps là đủ cho thanh phát.
    return ogg, int(len(ogg) * 8 / 16)


def natural_ssml(text: str) -> str:
    """Bọc câu vào SSML để giọng mẫu nghe như người thật, dùng cho AI Shadowing.

    Máy đọc văn bản trần thì chạy một mạch, đều đều — người học bắt chước theo sẽ học đúng
    cái nhịp máy đó. Thêm quãng nghỉ ở dấu câu và giữa các câu là thứ rẻ nhất mà thay đổi
    nhiều nhất: nó chia lời nói thành cụm nghĩa, đúng chỗ người thật lấy hơi.
    """
    import html
    safe = html.escape(' '.join((text or '').split()))
    # Nghỉ dài giữa câu, nghỉ ngắn ở dấu phẩy/chấm phẩy — đúng chỗ người nói lấy hơi.
    safe = re.sub(r'([.!?])\s+', r'\1<break time="450ms"/> ', safe)
    safe = re.sub(r'([,;:])\s+', r'\1<break time="220ms"/> ', safe)
    return f'<speak>{safe}</speak>'


def natural_model_audio(text: str, voice: str = None):
    """(ogg_opus_bytes, duration_ms) — bản đọc mẫu TỰ NHIÊN cho shadowing và "Nghe mẫu".

    Ưu tiên giọng Chirp3-HD tương ứng (xem NATURAL_VOICE): nó ngắt nghỉ và lên xuống giọng
    như người thật, đúng thứ feedback 09/09 yêu cầu. Chirp không nhận SSML nên đường này
    gửi văn bản trần và chỉ hạ tốc độ.

    Chirp lỗi thì lùi về Neural2 + SSML như trước, chứ không để mất luôn nút "Nghe mẫu":
    giọng cũ nghe máy móc hơn nhưng vẫn dùng được.
    """
    voice = voice or default_voice()
    if engine() != 'google' or voice not in GOOGLE_VOICES:
        return synthesize(text, voice)
    from app.utils import google_tts
    from app.utils.speaking_audio import probe_duration_ms, to_opus
    meta = GOOGLE_VOICES[voice]

    natural = NATURAL_VOICE.get(voice)
    attempts = []
    if natural:
        attempts.append((natural, NATURAL_RATE, None))
    # Dự phòng: giọng phòng thi, chậm hơn 10% và có quãng nghỉ ở dấu câu.
    attempts.append((voice, round(meta.get('rate', 0.9) * 0.9, 2), natural_ssml(text)))

    mp3 = None
    for idx, (name, rate, ssml) in enumerate(attempts):
        try:
            mp3 = google_tts.synthesize(text, name, meta['lang'], rate, ssml=ssml)
            break
        except google_tts.GoogleTtsNotConfigured as e:
            raise TtsNotConfigured(str(e)) from e
        except google_tts.GoogleTtsError as e:
            if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
                raise TtsQuota(str(e)) from e
            if idx + 1 < len(attempts):
                logger.warning("Natural voice %s failed, falling back to the exam voice: %s", name, e)
                continue
            raise TtsError(str(e)) from e

    ogg, ext = to_opus(mp3)
    if not ext:
        raise TtsError("ffmpeg could not convert Google MP3 to Opus")
    ms = probe_duration_ms(ogg)
    return ogg, ms if ms else int(len(ogg) * 8 / 16)


def _google(text: str, voice: str):
    """(ogg_opus_bytes, duration_ms) từ Google Cloud TTS."""
    from app.utils import google_tts
    from app.utils.speaking_audio import probe_duration_ms, to_opus

    meta = GOOGLE_VOICES[voice]
    try:
        mp3 = google_tts.synthesize(text, voice, meta['lang'], meta.get('rate', 0.9))
    except google_tts.GoogleTtsNotConfigured as e:
        raise TtsNotConfigured(str(e)) from e
    except google_tts.GoogleTtsError as e:
        # Cloud TTS trả 429 khi vượt giới hạn theo phút; job dừng lại chờ thay vì đánh dấu
        # hỏng hàng loạt, giống cách xử lý hạn mức của Gemini.
        if '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e):
            raise TtsQuota(str(e)) from e
        raise TtsError(str(e)) from e

    ogg, ext = to_opus(mp3)
    if not ext:
        raise TtsError("ffmpeg could not convert Google MP3 to Opus")
    # Đo bằng ffprobe chứ KHÔNG suy từ kích thước file: Opus nén biến thiên nên phép ước
    # lượng cũ (`len * 8 / 16`) lệch rất xa, mà phòng thi lấy con số này làm đồng hồ chặn.
    ms = probe_duration_ms(ogg)
    return ogg, ms if ms else int(len(ogg) * 8 / 16)


def synthesize(text: str, voice: str = None):
    """(ogg_opus_bytes, duration_ms) cho một lời của giám khảo."""
    voice = voice or default_voice()
    if voice not in voice_names():
        raise TtsError(f"invalid voice: {voice}")
    spoken = ' '.join((text or '').split())
    if not spoken:
        raise TtsError("nothing to read")
    if engine() == 'google':
        return _google(spoken, voice)
    if engine() == 'polly':
        return _polly(spoken, voice)
    pcm = _pcm(spoken, voice)
    return _to_opus(pcm), duration_ms(len(pcm))


def is_configured() -> bool:
    if engine() == 'google':
        from app.utils import google_tts
        return google_tts.is_configured()
    if engine() == 'polly':
        return bool(os.getenv('AWS_ACCESS_KEY_ID') and os.getenv('AWS_SECRET_ACCESS_KEY'))
    return bool(os.getenv("GEMINI_API_KEY"))
