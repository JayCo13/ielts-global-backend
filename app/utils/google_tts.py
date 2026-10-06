"""Gọi Google Cloud Text-to-Speech bằng service account, không cần thư viện google-*.

Vì sao tự làm phần xác thực: image backend hiện KHÔNG build lại được (máy chủ không ra
được Docker Hub, xem ghi chú deploy), nên không cài thêm được `google-auth`. Container lại
đã có sẵn `cryptography` và `python-jose` ký được RS256 — đủ để tự tạo JWT rồi đổi lấy
access token đúng quy trình OAuth2 của Google.

Vì sao là Google chứ không phải Gemini TTS: Gemini bị trần 100 request/ngày cho mỗi model
và giọng của nó không gắn với accent nào. Cloud TTS có sẵn giọng en-GB/en-US/en-AU theo
giới tính, không trần ngày, và dùng chung tài khoản Google mà hệ thống đã trả tiền.

Global port: GOOGLE_TTS_CREDENTIALS may be EITHER a path to the service-account JSON file
(as on the VN VPS) OR the JSON document itself pasted into the env var — Koyeb has no
persistent files, so inline JSON is the practical option there. Unset → not configured,
and every caller turns that into a clean "TTS not configured" error.
"""
import json
import os
import threading
import time

import requests
from jose import jwt

SCOPE = 'https://www.googleapis.com/auth/cloud-platform'
SYNTH_URL = 'https://texttospeech.googleapis.com/v1/text:synthesize'
VOICES_URL = 'https://texttospeech.googleapis.com/v1/voices'
# Bắt tay ngắn, đọc dài — cùng lý do như gemini_client: đường ra quốc tế hay rớt gói, một
# cú bắt tay hỏng không được ngốn trọn ngân sách chờ.
TIMEOUT = (5, 60)

_lock = threading.Lock()
_token = {'value': None, 'expires': 0}


class GoogleTtsNotConfigured(Exception):
    """Chưa trỏ tới file service account."""


class GoogleTtsError(Exception):
    """Gọi Cloud TTS thất bại."""


def credentials_path() -> str:
    return os.getenv('GOOGLE_TTS_CREDENTIALS', '') or ''


def _inline_json(value: str) -> bool:
    return value.lstrip().startswith('{')


def is_configured() -> bool:
    p = credentials_path()
    if not p:
        return False
    if _inline_json(p):
        try:
            sa = json.loads(p)
            return bool(sa.get('client_email') and sa.get('private_key'))
        except ValueError:
            return False
    return os.path.exists(p)


def _service_account() -> dict:
    path = credentials_path()
    if not path:
        raise GoogleTtsNotConfigured("GOOGLE_TTS_CREDENTIALS is not set")
    if _inline_json(path):
        try:
            return json.loads(path)
        except ValueError as e:
            raise GoogleTtsNotConfigured(f"GOOGLE_TTS_CREDENTIALS is not valid JSON: {e}")
    if not os.path.exists(path):
        raise GoogleTtsNotConfigured(f"Service account file not found: {path}")
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def access_token() -> str:
    """Token sống 1 giờ; giữ lại và chỉ lấy mới khi sắp hết hạn.

    Một lượt sinh cả kho là hàng nghìn lời gọi — xin token cho từng lời gọi vừa chậm vừa
    dễ bị Google chặn.
    """
    with _lock:
        if _token['value'] and time.time() < _token['expires'] - 60:
            return _token['value']

        sa = _service_account()
        now = int(time.time())
        assertion = jwt.encode(
            {'iss': sa['client_email'], 'scope': SCOPE, 'aud': sa['token_uri'],
             'iat': now, 'exp': now + 3600},
            sa['private_key'], algorithm='RS256',
            headers={'kid': sa.get('private_key_id')})
        resp = requests.post(sa['token_uri'], timeout=TIMEOUT, data={
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': assertion})
        if not resp.ok:
            raise GoogleTtsError(f"Could not get an access token: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        _token['value'] = data['access_token']
        _token['expires'] = time.time() + int(data.get('expires_in', 3600))
        return _token['value']


def _headers():
    return {'Authorization': 'Bearer ' + access_token(),
            'Content-Type': 'application/json; charset=utf-8'}


def list_voices(language_code: str = None) -> list:
    params = {'languageCode': language_code} if language_code else {}
    resp = requests.get(VOICES_URL, params=params, headers=_headers(), timeout=TIMEOUT)
    if not resp.ok:
        raise GoogleTtsError(f"Could not list voices: {resp.status_code} "
                             f"{resp.text[:200]}")
    return resp.json().get('voices', [])


def synthesize(text: str, voice_name: str, language_code: str, speaking_rate: float = 1.0,
               ssml: str = None, pitch: float = None):
    """Trả về bytes MP3. Người gọi tự chuyển sang Opus cho đồng bộ với kho hiện có.

    `ssml` thay cho `text` khi cần điều khiển nhịp: shadowing cần chỗ ngắt nghỉ đúng cụm
    nghĩa thì người học mới bắt chước được, còn văn bản trần thì máy đọc một mạch.
    """
    body = {
        'input': ({'ssml': ssml} if ssml else {'text': text}),
        'voice': {'languageCode': language_code, 'name': voice_name},
        # MP3 để đi qua cùng đường ffmpeg đang dùng cho mọi clip khác.
        'audioConfig': {'audioEncoding': 'MP3', 'speakingRate': speaking_rate,
                        **({'pitch': pitch} if pitch is not None else {})},
    }
    resp = requests.post(SYNTH_URL, headers=_headers(), json=body, timeout=TIMEOUT)
    if not resp.ok:
        detail = ''
        try:
            err = resp.json().get('error', {})
            detail = f"{err.get('status')} {err.get('message', '')[:200]}"
        except ValueError:
            detail = resp.text[:200]
        raise GoogleTtsError(f"Cloud TTS error {resp.status_code}: {detail}")
    audio = resp.json().get('audioContent')
    if not audio:
        raise GoogleTtsError("Cloud TTS returned no audio")
    import base64
    return base64.b64decode(audio)
