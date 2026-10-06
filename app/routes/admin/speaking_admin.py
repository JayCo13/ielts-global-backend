"""Admin: the Speaking question bank (docs/speaking-spec.md §2–3).

Adding material is paste-driven: an admin drops in a block of text, previews exactly the
rows that will be created, then saves. Managing it is a single table across Part 1 and
Part 2 topics — Part 3 never gets a row of its own because it always belongs to the
Part 2 topic it was entered with.

Ported from the VN tree. Global additions: TTS work is only queued when a TTS engine is
configured (the app must run without one), and two job triggers replace VN's cron/CLI use —
there is no scheduler on Koyeb:
    POST /admin/speaking/forecast/decay              (VN, unchanged)
    POST /admin/speaking/jobs/prune-audio?commit=    (prune old recordings from R2)
    POST /admin/speaking/jobs/generate-pending?retry_failed=  (AI suggestions/vocab backlog)
    POST /admin/speaking/tts/generate                (VN, unchanged: missing/stale clips)
"""
import re
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from fastapi.responses import Response

from app.models.models import (
    SpeakingTopic, SpeakingQuestion, SpeakingSuggestion, SpeakingVocabulary,
    SpeakingTts, SpeakingPronUnit, SpeakingPronItem, User,
)
from app.jobs.speaking_generate import run as run_generation
from app.jobs.speaking_forecast import recompute_levels, refresh_topic, run_decay
from app.jobs.speaking_tts_generate import run as run_tts, wanted_clips
from app.utils.speaking_tts import (default_voice, text_fingerprint,
                                    voice_catalog, voice_names)
from app.utils import speaking_tts
from app.routes.admin.auth import get_current_admin
from app.utils.datetime_utils import get_vietnam_time
from app.utils.speaking_parser import parse_bundle, parse_part1, parse_part2, parse_questions

router = APIRouter()

CATEGORIES = ('place', 'people', 'education', 'recreation', 'object', 'others')


# ── Thời gian xuất hiện: admins type "05/2026 - 08/2026" and paste it around ──
_MONTH = re.compile(r'(\d{1,2})\s*[/-]\s*(\d{4})')


def parse_window(text: Optional[str]):
    """('05/2026 – 08/2026') -> (date(2026,5,1), date(2026,8,31)). Tolerates one month."""
    if not text:
        return None, None
    found = _MONTH.findall(text)
    if not found:
        return None, None

    def first_of(m, y):
        return date(int(y), max(1, min(12, int(m))), 1)

    start = first_of(*found[0])
    if len(found) > 1:
        m, y = found[1]
        m, y = max(1, min(12, int(m))), int(y)
        # last day of that month, without pulling in calendar arithmetic elsewhere
        end = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        end = date.fromordinal(end.toordinal() - 1)
    else:
        end = date.fromordinal(date(start.year + (start.month == 12),
                                    1 if start.month == 12 else start.month + 1,
                                    1).toordinal() - 1)
    return start, end


def format_window(topic: SpeakingTopic) -> str:
    if not topic.appear_from:
        return ''
    a = topic.appear_from.strftime('%m/%Y')
    b = topic.appear_to.strftime('%m/%Y') if topic.appear_to else a
    return a if a == b else f'{a} - {b}'


class PreviewIn(BaseModel):
    part1_text: Optional[str] = None
    part2_text: Optional[str] = None
    followup_text: Optional[str] = None
    part3_text: Optional[str] = None


class SaveIn(PreviewIn):
    # Part 1
    is_important: bool = False
    work_study: str = 'neutral'
    # Part 2
    category: Optional[str] = None
    # Both
    occurrence_count: int = 0
    appear_window: Optional[str] = None


class TopicUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    work_study: Optional[str] = None
    is_important: Optional[bool] = None
    occurrence_count: Optional[int] = None
    appear_window: Optional[str] = None


@router.post("/speaking/preview", response_model=dict)
async def preview(payload: PreviewIn, current_admin: User = Depends(get_current_admin)):
    """Show the exact rows a save would create. Writes nothing."""
    parsed = parse_bundle(payload.part1_text, payload.part2_text,
                          payload.followup_text, payload.part3_text)
    if not parsed:
        raise HTTPException(status_code=400, detail="Chưa có nội dung nào để tách")
    return {"preview": parsed}


def _now():
    return get_vietnam_time().replace(tzinfo=None)


@router.post("/speaking/topics", response_model=dict)
async def create_topics(payload: SaveIn,
                        background: BackgroundTasks,
                        current_admin: User = Depends(get_current_admin),
                        db: Session = Depends(get_db)):
    """Create a Part 1 topic, a Part 2 package, or both from one paste."""
    start, end = parse_window(payload.appear_window)
    created = []

    if payload.part1_text:
        parsed = parse_part1(payload.part1_text)
        if not parsed['title']:
            raise HTTPException(status_code=400, detail="Part 1 chưa có tên topic")
        topic = SpeakingTopic(
            part='part1', title=parsed['title'][:255],
            work_study=payload.work_study if payload.work_study in ('work', 'study', 'neutral') else 'neutral',
            is_important=payload.is_important,
            occurrence_count=max(0, payload.occurrence_count),
            appear_from=start, appear_to=end,
            last_updated=_now(), created_by=current_admin.user_id,
        )
        db.add(topic)
        db.flush()
        for i, q in enumerate(parsed['questions']):
            db.add(SpeakingQuestion(topic_id=topic.topic_id, part='part1',
                                    order_index=i, content=q))
        created.append({'topic_id': topic.topic_id, 'part': 'part1',
                        'title': topic.title, 'questions': len(parsed['questions'])})

    if payload.part2_text:
        parsed = parse_part2(payload.part2_text)
        if not parsed['title']:
            raise HTTPException(status_code=400, detail="Part 2 chưa có tên topic")
        if payload.category and payload.category not in CATEGORIES:
            raise HTTPException(status_code=400, detail="Nhóm chủ đề không hợp lệ")
        topic = SpeakingTopic(
            part='part2', title=parsed['title'][:255],
            category=payload.category, cue_card=parsed['cue_card'],
            occurrence_count=max(0, payload.occurrence_count),
            appear_from=start, appear_to=end,
            last_updated=_now(), created_by=current_admin.user_id,
        )
        db.add(topic)
        db.flush()
        # The long turn is a question in its own right: generated content hangs off a
        # question_id, and so will the student's recording.
        db.add(SpeakingQuestion(topic_id=topic.topic_id, part='part2',
                                order_index=0, content=parsed['cue_card']))
        followups = parse_questions(payload.followup_text)
        for i, q in enumerate(followups):
            db.add(SpeakingQuestion(topic_id=topic.topic_id, part='part2_followup',
                                    order_index=i, content=q))
        part3 = parse_questions(payload.part3_text)
        for i, q in enumerate(part3):
            db.add(SpeakingQuestion(topic_id=topic.topic_id, part='part3',
                                    order_index=i, content=q))
        created.append({'topic_id': topic.topic_id, 'part': 'part2', 'title': topic.title,
                        'followups': len(followups), 'part3': len(part3)})

    if not created:
        raise HTTPException(status_code=400, detail="Chưa có nội dung nào để lưu")

    # Stars are a percentile across every topic of the same part, so adding one topic
    # re-ranks the rest. Without this the Forecast column just shows a dash until
    # somebody happens to press decay.
    db.flush()
    recompute_levels(db)
    db.commit()

    # Generation is two Gemini calls per question — a ten-question Part 1 topic would
    # keep the admin waiting minutes. Kick it off behind the response; each question
    # carries its own status so the management table can show progress.
    for c in created:
        background.add_task(run_generation, c['topic_id'], None, False, 0, False)
        # The examiner has to be able to read the new questions aloud, so voice the topic
        # in the same pass rather than leaving it for someone to remember later.
        # Global: only when a TTS engine is configured — otherwise every clip would just
        # be logged as a failure.
        if speaking_tts.is_configured():
            background.add_task(run_tts, c['topic_id'], None, False, 0, False)

    return {"created": created}


@router.get("/speaking/topics", response_model=dict)
async def list_topics(q: Optional[str] = None, part: Optional[str] = None,
                      current_admin: User = Depends(get_current_admin),
                      db: Session = Depends(get_db)):
    """The management table. Part 3 is intentionally absent — it rides on its Part 2."""
    query = db.query(SpeakingTopic)
    if part in ('part1', 'part2'):
        query = query.filter(SpeakingTopic.part == part)
    if q:
        query = query.filter(or_(SpeakingTopic.title.ilike(f'%{q}%'),
                                 SpeakingTopic.cue_card.ilike(f'%{q}%')))
    topics = query.order_by(SpeakingTopic.topic_id.desc()).all()

    counts = dict(
        db.query(SpeakingQuestion.topic_id, func.count(SpeakingQuestion.question_id))
        .group_by(SpeakingQuestion.topic_id).all()
    )
    # Generation progress per topic, so the table can show "7/10 đã sinh" without a
    # request per row.
    gen = {}
    for tid, status, n in (db.query(SpeakingQuestion.topic_id, SpeakingQuestion.gen_status,
                                    func.count(SpeakingQuestion.question_id))
                           .group_by(SpeakingQuestion.topic_id, SpeakingQuestion.gen_status).all()):
        gen.setdefault(tid, {})[status] = n
    return {"topics": [{
        'topic_id': t.topic_id,
        'part': t.part,
        'title': t.title,
        'category': t.category,
        'work_study': t.work_study,
        'is_important': bool(t.is_important),
        'occurrence_count': t.occurrence_count,
        'forecast_level': t.forecast_level,
        'is_active': bool(t.is_active),
        'appear_window': format_window(t),
        'question_count': counts.get(t.topic_id, 0),
        'gen': gen.get(t.topic_id, {}),
        'last_updated': t.last_updated.isoformat() if t.last_updated else None,
    } for t in topics]}


@router.get("/speaking/topics/{topic_id}", response_model=dict)
async def topic_detail(topic_id: int,
                       current_admin: User = Depends(get_current_admin),
                       db: Session = Depends(get_db)):
    topic = db.query(SpeakingTopic).filter(SpeakingTopic.topic_id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Không tìm thấy topic")
    questions = (db.query(SpeakingQuestion)
                 .filter(SpeakingQuestion.topic_id == topic_id)
                 .order_by(SpeakingQuestion.part, SpeakingQuestion.order_index).all())
    # Câu nào giám khảo đọc được, THEO TỪNG GIỌNG. Trước đây chỉ cần một giọng bất kỳ có
    # clip là nút loa sáng lên — đổi engine xong là bấm vào 404, vì clip cũ thuộc giọng cũ.
    keys = [f'question:{q.question_id}' for q in questions]
    voiced = {}
    if keys:
        for cache_key, voice in db.query(SpeakingTts.cache_key, SpeakingTts.voice).filter(
                SpeakingTts.cache_key.in_(keys), SpeakingTts.audio.isnot(None)).all():
            voiced.setdefault(cache_key, []).append(voice)
    current = list(voice_names())
    made = sum(len([v for v in voiced.get(k, []) if v in current]) for k in keys)
    return {
        'topic_id': topic.topic_id, 'part': topic.part, 'title': topic.title,
        'category': topic.category, 'work_study': topic.work_study,
        'is_important': bool(topic.is_important), 'cue_card': topic.cue_card,
        'occurrence_count': topic.occurrence_count,
        'appear_window': format_window(topic),
        'questions': [{'question_id': q.question_id, 'part': q.part,
                       'order_index': q.order_index, 'content': q.content,
                       'gen_status': q.gen_status, 'gen_error': q.gen_error,
                       'voiced_in': [v for v in voiced.get(f'question:{q.question_id}', [])
                                     if v in current],
                       'has_audio': bool([v for v in voiced.get(f'question:{q.question_id}', [])
                                          if v in current])}
                      for q in questions],
        # Để giao diện nói rõ "24/30 clip" thay vì để admin đoán xem đã xong chưa.
        'audio': {'made': made, 'total': len(questions) * len(current),
                  'voices': current},
    }


@router.put("/speaking/topics/{topic_id}", response_model=dict)
async def update_topic(topic_id: int, payload: TopicUpdate,
                       current_admin: User = Depends(get_current_admin),
                       db: Session = Depends(get_db)):
    topic = db.query(SpeakingTopic).filter(SpeakingTopic.topic_id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Không tìm thấy topic")

    if payload.title is not None:
        topic.title = payload.title[:255]
    if payload.category is not None:
        if payload.category and payload.category not in CATEGORIES:
            raise HTTPException(status_code=400, detail="Nhóm chủ đề không hợp lệ")
        topic.category = payload.category or None
    if payload.work_study is not None and payload.work_study in ('work', 'study', 'neutral'):
        topic.work_study = payload.work_study
    if payload.is_important is not None:
        topic.is_important = payload.is_important
    if payload.occurrence_count is not None:
        topic.occurrence_count = max(0, payload.occurrence_count)
    if payload.appear_window is not None:
        topic.appear_from, topic.appear_to = parse_window(payload.appear_window)
        # Sửa cửa sổ xong phải thấy ngay kết quả trong Forecast, không phải bấm thêm nút
        # decay mới đúng (feedback 31/08).
        refresh_topic(db, topic)

    topic.last_updated = _now()
    db.flush()
    recompute_levels(db)
    db.commit()
    return {"topic_id": topic.topic_id, "appear_window": format_window(topic),
            "forecast_level": topic.forecast_level}


@router.delete("/speaking/topics/{topic_id}", response_model=dict)
async def delete_topic(topic_id: int,
                       current_admin: User = Depends(get_current_admin),
                       db: Session = Depends(get_db)):
    topic = db.query(SpeakingTopic).filter(SpeakingTopic.topic_id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Không tìm thấy topic")
    db.delete(topic)      # questions/suggestions/vocabulary cascade
    db.commit()
    return {"deleted": topic_id}


# ── Nội dung sinh tự động (§2.3) ───────────────────────────────────────────────

class QuestionIn(BaseModel):
    content: str
    part: Optional[str] = None      # chỉ dùng khi thêm mới
    order_index: Optional[int] = None


QUESTION_PARTS = ('part1', 'part2', 'part2_followup', 'part3')


@router.put("/speaking/questions/{question_id}", response_model=dict)
async def edit_question(question_id: int, payload: QuestionIn,
                        background: BackgroundTasks,
                        current_admin: User = Depends(get_current_admin),
                        db: Session = Depends(get_db)):
    """Sửa nội dung một câu hỏi.

    Sửa chữ xong thì gợi ý và từ vựng cũ không còn đúng nữa, nên câu đó quay về hàng chờ
    sinh lại. Clip giọng giám khảo tự hết hiệu lực vì fingerprint đổi theo nội dung.
    """
    q = db.query(SpeakingQuestion).filter(SpeakingQuestion.question_id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu hỏi")
    content = (payload.content or '').strip()
    if not content:
        raise HTTPException(status_code=400, detail="Nội dung câu hỏi không được để trống")
    if content == q.content:
        return {"question_id": q.question_id, "content": q.content, "unchanged": True}

    q.content = content
    if payload.order_index is not None:
        q.order_index = max(0, payload.order_index)
    q.gen_status = 'pending'
    q.gen_error = None
    # Câu hỏi Part 2 chính là cue card của topic — giữ hai chỗ khớp nhau.
    if q.part == 'part2':
        topic = db.query(SpeakingTopic).filter(SpeakingTopic.topic_id == q.topic_id).first()
        if topic:
            topic.cue_card = content
    db.commit()
    background.add_task(_regenerate, q.topic_id, question_id)
    return {"question_id": q.question_id, "content": q.content, "gen_status": q.gen_status}


@router.post("/speaking/topics/{topic_id}/questions", response_model=dict)
async def add_question(topic_id: int, payload: QuestionIn,
                       background: BackgroundTasks,
                       current_admin: User = Depends(get_current_admin),
                       db: Session = Depends(get_db)):
    """Thêm một câu hỏi vào topic đã có."""
    topic = db.query(SpeakingTopic).filter(SpeakingTopic.topic_id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Không tìm thấy topic")
    content = (payload.content or '').strip()
    if not content:
        raise HTTPException(status_code=400, detail="Nội dung câu hỏi không được để trống")
    part = payload.part or ('part1' if topic.part == 'part1' else 'part3')
    if part not in QUESTION_PARTS:
        raise HTTPException(status_code=400, detail="Phần không hợp lệ")
    # Part 2 chỉ có đúng một câu — chính là cue card.
    if part == 'part2':
        raise HTTPException(status_code=400,
                            detail="Phần nói dài chỉ có một đề bài; hãy sửa cue card thay vì thêm câu.")

    last = db.query(func.max(SpeakingQuestion.order_index)).filter(
        SpeakingQuestion.topic_id == topic_id, SpeakingQuestion.part == part).scalar()
    q = SpeakingQuestion(topic_id=topic_id, part=part, content=content,
                         order_index=(last + 1) if last is not None else 0)
    db.add(q)
    topic.last_updated = _now()
    db.commit()
    db.refresh(q)
    background.add_task(_regenerate, topic_id, q.question_id)
    return {"question_id": q.question_id, "part": q.part, "content": q.content,
            "order_index": q.order_index, "gen_status": q.gen_status}


def _regenerate(topic_id: int, question_id: int):
    """Sinh lại gợi ý/từ vựng và clip giọng cho đúng một câu vừa đổi."""
    try:
        run_generation(question_id=question_id)
    except Exception:
        pass          # trạng thái lỗi đã nằm trên chính dòng câu hỏi
    if not speaking_tts.is_configured():
        return
    try:
        run_tts(topic_id=topic_id)
    except Exception:
        pass


@router.post("/speaking/topics/{topic_id}/generate", response_model=dict)
async def generate_topic(topic_id: int, background: BackgroundTasks,
                         retry_failed: bool = True,
                         current_admin: User = Depends(get_current_admin),
                         db: Session = Depends(get_db)):
    """Re-run generation for everything in a topic that isn't done."""
    topic = db.query(SpeakingTopic).filter(SpeakingTopic.topic_id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Không tìm thấy topic")
    queued = (db.query(func.count(SpeakingQuestion.question_id))
              .filter(SpeakingQuestion.topic_id == topic_id,
                      SpeakingQuestion.gen_status != 'done').scalar()) or 0
    background.add_task(run_generation, topic_id, None, retry_failed, 0, False)
    return {"topic_id": topic_id, "queued": queued}


@router.post("/speaking/questions/{question_id}/generate", response_model=dict)
async def generate_question(question_id: int, background: BackgroundTasks,
                            current_admin: User = Depends(get_current_admin),
                            db: Session = Depends(get_db)):
    """Re-generate one question, whatever state it is in — the "làm lại câu này" button."""
    question = db.query(SpeakingQuestion).filter(
        SpeakingQuestion.question_id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu hỏi")
    question.gen_status = 'pending'
    question.gen_error = None
    db.add(question)
    db.commit()
    background.add_task(run_generation, None, question_id, False, 0, False)
    return {"question_id": question_id, "status": "pending"}


@router.get("/speaking/questions/{question_id}/generated", response_model=dict)
async def question_generated(question_id: int,
                             current_admin: User = Depends(get_current_admin),
                             db: Session = Depends(get_db)):
    """The outline, the four band answers and the vocabulary for one question."""
    question = db.query(SpeakingQuestion).filter(
        SpeakingQuestion.question_id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu hỏi")

    sug = db.query(SpeakingSuggestion).filter(
        SpeakingSuggestion.question_id == question_id).first()
    vocab = (db.query(SpeakingVocabulary)
             .filter(SpeakingVocabulary.question_id == question_id)
             .order_by(SpeakingVocabulary.band_level, SpeakingVocabulary.order_index).all())

    by_band = {}
    for v in vocab:
        by_band.setdefault(v.band_level, []).append(
            {'term': v.term, 'meaning_vi': v.meaning_vi, 'example': v.example})

    return {
        'question_id': question_id,
        'part': question.part,
        'content': question.content,
        'gen_status': question.gen_status,
        'gen_error': question.gen_error,
        'outline': sug.outline if sug else None,
        'samples': sug.samples if sug else None,
        'model': sug.model if sug else None,
        'vocabulary': by_band,
    }


# ── Giọng Examiner (§4.3, quyết định #3) ──────────────────────────────────────

@router.get("/speaking/tts/status", response_model=dict)
async def tts_status(current_admin: User = Depends(get_current_admin),
                     db: Session = Depends(get_db)):
    """How much of the bank already has audio, per voice."""
    clips = wanted_clips(db)
    keys = [k for k, _ in clips]
    have = {}
    if keys:
        for voice, n in (db.query(SpeakingTts.voice, func.count(SpeakingTts.id))
                         .filter(SpeakingTts.cache_key.in_(keys))
                         .group_by(SpeakingTts.voice).all()):
            have[voice] = n
    total_bytes = db.query(func.coalesce(func.sum(func.length(SpeakingTts.audio)), 0)).scalar() or 0
    return {
        "voices": list(voice_names()),
        "voice_catalog": voice_catalog(),
        "default_voice": default_voice(),
        "lines": len(clips),
        "have": have,
        # Global: lets the admin page explain why nothing gets generated.
        "engine": speaking_tts.engine(),
        "configured": speaking_tts.is_configured(),
        "total_mb": round(total_bytes / 1048576, 1),
    }


@router.post("/speaking/tts/generate", response_model=dict)
async def tts_generate(background: BackgroundTasks, topic_id: Optional[int] = None,
                       voice: Optional[str] = None,
                       current_admin: User = Depends(get_current_admin),
                       db: Session = Depends(get_db)):
    """Sinh những clip còn thiếu hoặc đã lỗi thời, cho một topic hoặc cả kho.

    Trả về số clip còn thiếu để giao diện báo được "đang sinh N clip" — chạy nền nên nếu
    không nói gì thì admin bấm xong không biết có gì đang xảy ra hay không.
    """
    if voice and voice not in voice_names():
        raise HTTPException(status_code=400, detail="Giọng không hợp lệ")
    if not speaking_tts.is_configured():
        raise HTTPException(
            status_code=503,
            detail=f"TTS is not configured (SPEAKING_TTS_ENGINE={speaking_tts.engine()}). "
                   "Set GEMINI_API_KEY, GOOGLE_TTS_CREDENTIALS or AWS keys for that engine.")
    voices = [voice] if voice else list(voice_names())
    clips = wanted_clips(db, topic_id)
    have = {(r.cache_key, r.voice): r for r in db.query(SpeakingTts).filter(
        SpeakingTts.cache_key.in_([k for k, _ in clips])).all()} if clips else {}
    missing = 0
    for cache_key, text in clips:
        for v in voices:
            row = have.get((cache_key, v))
            if not row or not row.audio or row.fingerprint != text_fingerprint(text, v):
                missing += 1
    background.add_task(run_tts, topic_id, [voice] if voice else None, False, 0, False)
    return {"topic_id": topic_id, "voice": voice or "tất cả", "started": True,
            "missing": missing}


@router.get("/speaking/tts/audio")
async def tts_audio(request: Request, key: str, voice: str = None,
                    token: Optional[str] = None,
                    db: Session = Depends(get_db)):
    """Play one clip. `key` is the flat cache key, e.g. "question:12", "script:opening".

    An <audio> element cannot send an Authorization header, so the token may ride in the
    query string — the same arrangement the listening review player already uses.
    """
    from jose import jwt, JWTError
    from app.routes.admin.auth import SECRET_KEY, ALGORITHM

    if not token:
        auth = request.headers.get("authorization", "")
        token = auth.replace("Bearer ", "") if auth else None
    if not token:
        raise HTTPException(status_code=401, detail="Thiếu token")
    try:
        username = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token không hợp lệ")
    user = db.query(User).filter(User.username == username).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Chỉ dành cho admin")

    row = db.query(SpeakingTts).filter(SpeakingTts.cache_key == key,
                                       SpeakingTts.voice == (voice or default_voice())).first()
    if not row or not row.audio:
        raise HTTPException(status_code=404, detail="Chưa có audio cho câu này")
    return Response(content=row.audio, media_type="audio/ogg",
                    headers={"Cache-Control": "private, max-age=86400",
                             "X-Duration-Ms": str(row.duration_ms or 0)})


@router.post("/speaking/forecast/decay", response_model=dict)
async def speaking_decay(current_admin: User = Depends(get_current_admin),
                         db: Session = Depends(get_db)):
    """The "chạy decay" button (docs/speaking-spec.md §3).

    Speaking decays on its appearance window alone — never on last_updated, so an admin
    fixing a typo must not extend how long a topic is predicted for. Part 3 is not
    handled separately: its questions hang off a Part 2 topic and inherit its state.
    """
    return run_decay(db)


# ── Job triggers (global: no cron on Koyeb) ─────────────────────────────────────────────

@router.post("/speaking/jobs/prune-audio", response_model=dict)
async def speaking_prune_audio(commit: bool = False,
                               current_admin: User = Depends(get_current_admin)):
    """Prune old Speaking recordings from R2 (VN: `python -m app.jobs.prune_speaking_audio`).

    Dry run by default; `?commit=true` deletes. Transcripts, scores and feedback are kept
    forever — only the audio goes, and the rows are marked `audio_expired`.
    """
    from app.jobs.prune_speaking_audio import run as run_prune
    return await run_in_threadpool(run_prune, commit, verbose=False)


@router.post("/speaking/jobs/generate-pending", response_model=dict)
async def speaking_generate_pending(background: BackgroundTasks, retry_failed: bool = False,
                                    current_admin: User = Depends(get_current_admin),
                                    db: Session = Depends(get_db)):
    """Queue AI generation (outline, model answers, vocabulary) for every question still
    pending — and failed ones with `?retry_failed=true`. Also picks up rows stranded in
    'running' by a restart (VN: `python -m app.jobs.speaking_generate`).

    Runs as a BackgroundTask: two Gemini calls per question, so a large backlog can take a
    long time and is cut short if the Koyeb instance restarts — just trigger it again, the
    per-question status makes it resumable.
    """
    from app.jobs.speaking_generate import pending_query
    queued = pending_query(db, retry_failed=retry_failed).count()
    background.add_task(run_generation, None, None, retry_failed, 0, False)
    return {"queued": queued, "retry_failed": retry_failed}


# ── Pronunciation Lessons (feedback 09/09) ─────────────────────────────────────────────
#
# Admin nhập tay tiêu đề + lý thuyết; phần luyện tập thì bấm một nút cho AI sinh từ chính
# lý thuyết đó. Đây là bộ MẶC ĐỊNH mà mọi học viên thấy — học viên VIP bấm "Làm mới" sẽ
# có bộ riêng, không đụng vào bộ này (app/routes/student/speaking_lessons.py).

class PronUnitIn(BaseModel):
    title: str
    theory: Optional[str] = None
    order_index: Optional[int] = None
    is_published: Optional[bool] = None


def _pron_unit(db: Session, unit_id: int) -> SpeakingPronUnit:
    unit = db.query(SpeakingPronUnit).filter(
        SpeakingPronUnit.unit_id == unit_id).first()
    if not unit:
        raise HTTPException(status_code=404, detail="Không tìm thấy bài học.")
    return unit


def _pron_default_items(db: Session, unit_id: int):
    return (db.query(SpeakingPronItem)
            .filter(SpeakingPronItem.unit_id == unit_id,
                    SpeakingPronItem.user_id.is_(None))
            .order_by(SpeakingPronItem.order_index, SpeakingPronItem.item_id).all())


@router.get("/speaking/pron-units", response_model=dict)
async def list_pron_units(current_admin: User = Depends(get_current_admin),
                          db: Session = Depends(get_db)):
    units = (db.query(SpeakingPronUnit)
             .order_by(SpeakingPronUnit.order_index, SpeakingPronUnit.unit_id).all())
    counts = dict(db.query(SpeakingPronItem.unit_id,
                           func.count(SpeakingPronItem.item_id))
                  .filter(SpeakingPronItem.user_id.is_(None))
                  .group_by(SpeakingPronItem.unit_id).all())
    return {"units": [{"unit_id": u.unit_id, "title": u.title,
                       "order_index": u.order_index,
                       "is_published": bool(u.is_published),
                       "practice_count": counts.get(u.unit_id, 0),
                       "updated_at": u.updated_at.isoformat() if u.updated_at else None}
                      for u in units]}


@router.get("/speaking/pron-units/{unit_id}", response_model=dict)
async def pron_unit_detail(unit_id: int,
                           current_admin: User = Depends(get_current_admin),
                           db: Session = Depends(get_db)):
    unit = _pron_unit(db, unit_id)
    return {"unit_id": unit.unit_id, "title": unit.title, "theory": unit.theory,
            "order_index": unit.order_index, "is_published": bool(unit.is_published),
            "items": [{"item_id": i.item_id, "kind": i.kind, "content": i.content,
                       "note": i.note} for i in _pron_default_items(db, unit_id)]}


@router.post("/speaking/pron-units", response_model=dict)
async def create_pron_unit(payload: PronUnitIn,
                           current_admin: User = Depends(get_current_admin),
                           db: Session = Depends(get_db)):
    title = ' '.join((payload.title or '').split())
    if not title:
        raise HTTPException(status_code=400, detail="Thiếu tiêu đề Unit.")
    order = payload.order_index
    if order is None:
        order = (db.query(func.coalesce(func.max(SpeakingPronUnit.order_index), 0))
                 .scalar() or 0) + 1
    unit = SpeakingPronUnit(title=title[:255], theory=payload.theory or None,
                            order_index=order,
                            is_published=bool(payload.is_published))
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return {"unit_id": unit.unit_id}


@router.put("/speaking/pron-units/{unit_id}", response_model=dict)
async def update_pron_unit(unit_id: int, payload: PronUnitIn,
                           current_admin: User = Depends(get_current_admin),
                           db: Session = Depends(get_db)):
    unit = _pron_unit(db, unit_id)
    title = ' '.join((payload.title or '').split())
    if title:
        unit.title = title[:255]
    if payload.theory is not None:
        unit.theory = payload.theory or None
    if payload.order_index is not None:
        unit.order_index = payload.order_index
    if payload.is_published is not None:
        unit.is_published = bool(payload.is_published)
    db.commit()
    return {"unit_id": unit.unit_id}


@router.delete("/speaking/pron-units/{unit_id}", response_model=dict)
async def delete_pron_unit(unit_id: int,
                           current_admin: User = Depends(get_current_admin),
                           db: Session = Depends(get_db)):
    unit = _pron_unit(db, unit_id)
    # Xoá theo unit_id, không bao giờ delete() trần — kể cả khi FK đã có ON DELETE CASCADE.
    db.query(SpeakingPronItem).filter(
        SpeakingPronItem.unit_id == unit_id).delete(synchronize_session=False)
    db.delete(unit)
    db.commit()
    return {"deleted": unit_id}


@router.post("/speaking/pron-units/{unit_id}/generate", response_model=dict)
async def generate_pron_items(unit_id: int,
                              current_admin: User = Depends(get_current_admin),
                              db: Session = Depends(get_db)):
    """Sinh lại bộ luyện tập MẶC ĐỊNH của Unit từ phần lý thuyết.

    Chạy đồng bộ chứ không đẩy nền: admin bấm xong là muốn xem ngay bộ vừa ra để duyệt,
    và một lần gọi model đọc chữ chỉ mất vài giây.
    """
    from app.utils import speaking_improve as I
    from app.routes.student.speaking_lessons import _clean_items

    unit = _pron_unit(db, unit_id)
    if not (unit.theory or '').strip():
        raise HTTPException(status_code=400,
                            detail="Hãy nhập phần lý thuyết trước — AI dựa vào đó để ra bài.")
    try:
        data = I.lesson_items(unit.title, unit.theory or '', I.LESSON_BATCH,
                              [i.content for i in _pron_default_items(db, unit_id)])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"AI đang bận: {e}")

    rows = _clean_items(data)
    if not rows:
        raise HTTPException(status_code=503, detail="AI chưa trả về nội dung dùng được.")

    db.query(SpeakingPronItem).filter(
        SpeakingPronItem.unit_id == unit_id,
        SpeakingPronItem.user_id.is_(None)).delete(synchronize_session=False)
    for i, row in enumerate(rows):
        db.add(SpeakingPronItem(unit_id=unit_id, user_id=None, order_index=i, **row))
    db.commit()
    return {"items": [{"item_id": i.item_id, "kind": i.kind, "content": i.content,
                       "note": i.note} for i in _pron_default_items(db, unit_id)]}
