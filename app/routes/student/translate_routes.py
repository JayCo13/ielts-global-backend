"""Server-side proxy for the in-browser translator/dictionary (student app).

Ported from the Vietnam tree (ielts-main-nov). The student site used to call Groq
directly from the browser with a REACT_APP_GROQ_API_KEY baked into the public
bundle, i.e. the key was readable by anyone. These endpoints move that call
server-side so the browser never holds a Groq key.

Key: GROQ_TRANSLATE_API_KEY (dedicated, rotatable on its own), falling back to
GROQ_API_KEY. Global differs from VN in that the target language is chosen by the
student (see SUPPORTED_LANGUAGES in translatorService.js), so it is a request field.

Auth-gated with get_current_student so the proxy can't be used as a free public
translation API.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import os
import json
import groq
import httpx

from app.routes.admin.auth import get_current_student
from app.models.models import User
from app.utils.datetime_utils import get_vietnam_time

router = APIRouter()

# Groq retired the Llama 3.x line (llama-3.1-8b-instant now 404s with
# model_not_found). gpt-oss-20b is the fastest remaining chat model on Groq that
# returns clean raw JSON for the dictionary prompt.
MODEL = "openai/gpt-oss-20b"

_RETRIES = 3
_TIMEOUT = httpx.Timeout(20.0, connect=4.0)


def _client() -> groq.Groq:
    # Lazy init so the app still boots if the key isn't configured; the error
    # only surfaces when the feature is actually used.
    key = os.getenv("GROQ_TRANSLATE_API_KEY") or os.getenv("GROQ_API_KEY")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="Translation service is not configured.",
        )
    return groq.Groq(api_key=key, max_retries=_RETRIES, timeout=_TIMEOUT)


def _clean_lang(name: str, default: str) -> str:
    # The language name is interpolated into the prompt; keep it short and plain.
    name = (name or "").strip()[:40]
    return name if name and all(c.isalpha() or c in " -()" for c in name) else default


class TranslateRequest(BaseModel):
    text: str
    sourceLanguage: str = "English"
    targetLanguage: str = "Vietnamese"


class DictionaryRequest(BaseModel):
    word: str
    targetLanguage: str = "Vietnamese"


@router.post("/translate")
async def translate_text(
    body: TranslateRequest,
    current_student: User = Depends(get_current_student),
):
    text = (body.text or "").strip()[:2000]
    if not text:
        raise HTTPException(status_code=400, detail="Text to translate cannot be empty")
    source = _clean_lang(body.sourceLanguage, "English")
    target = _clean_lang(body.targetLanguage, "Vietnamese")

    prompt = (
        f"Translate the following {source} text to {target}. "
        "Provide only the translation without any additional explanation or formatting. "
        "Consider the context and provide the most appropriate translation:\n\n"
        f'"{text}"'
    )

    try:
        resp = _client().chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a professional translator specializing in English to "
                        f"{target} translation. Provide accurate, contextually appropriate "
                        "translations. For IELTS exam content, maintain the academic tone and "
                        "precision."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=500,
            top_p=1,
            stream=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Translation failed: {e}")

    translation = (resp.choices[0].message.content or "").strip()
    cleaned = translation.strip("\"'")

    return {
        "originalText": text,
        "translatedText": cleaned,
        "sourceLanguage": source,
        "targetLanguage": target,
        "timestamp": get_vietnam_time().isoformat(),
    }


@router.post("/dictionary")
async def dictionary_lookup(
    body: DictionaryRequest,
    current_student: User = Depends(get_current_student),
):
    word = (body.word or "").strip()[:100]
    if not word:
        raise HTTPException(status_code=400, detail="Word cannot be empty")
    target = _clean_lang(body.targetLanguage, "Vietnamese")

    prompt = (
        f'Provide a detailed dictionary entry for the English word "{word}". '
        "Return ONLY a valid JSON object with this exact structure (no markdown, no code "
        "blocks, just raw JSON):\n"
        "{\n"
        f'  "word": "{word}",\n'
        '  "phonetics": {\n'
        '    "uk": "/phonetic transcription UK/",\n'
        '    "us": "/phonetic transcription US/"\n'
        "  },\n"
        '  "meanings": [\n'
        "    {\n"
        f'      "partOfSpeech": "part of speech in {target}",\n'
        '      "definitions": [\n'
        "        {\n"
        f'          "meaning": "{target} translation/definition",\n'
        '          "example": "Example sentence in English if available",\n'
        f'          "exampleTrans": "{target} translation of example"\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Use IPA for phonetics\n"
        f"- Translate part of speech to {target}\n"
        f"- Provide {target} meanings/definitions\n"
        "- Include examples when relevant\n"
        "- Return ONLY the JSON object, no other text"
    )

    try:
        resp = _client().chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a professional English-{target} dictionary. Return ONLY "
                        "valid JSON with no markdown formatting."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1000,
            top_p=1,
            stream=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dictionary lookup failed: {e}")

    content = (resp.choices[0].message.content or "").strip()
    if "```" in content:
        content = content.replace("```json", "").replace("```", "").strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Invalid response format from dictionary service")
