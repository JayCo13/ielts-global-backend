"""Building one Speaking paper (docs/speaking-spec.md §4.3).

The result is a flat list of *steps* the exam room walks from top to bottom — an examiner
line to play, a question to ask, the Part 2 long turn — rather than a nested structure the
frontend would have to know how to traverse. Everything the room needs to render and time
a step is on the step itself, including which pre-generated clip to play, so adding a new
kind of step later does not mean teaching the room a new special case.

Two rules from the spec drive most of the code here:

* **Questions are never shuffled.** Within a topic they are asked top-down in the order the
  admin typed them. Only *which topics* are picked varies.
* **A thin bank must never block a test.** Every filter (forecast month, "đề chưa làm",
  work/study exclusion) narrows a preference, then falls back to the wider pool if that
  leaves nothing. Failing to build a paper is only acceptable when the bank is truly empty.
"""
import random
from datetime import date
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import (
    SpeakingAttempt, SpeakingAttemptAnswer, SpeakingQuestion, SpeakingTopic,
)
from app.utils import speaking_scripts as S
from app.utils.speaking_tts import default_voice

# §4.3 — Part 1 uses three topics: one "important" one, then two others.
PART1_TOPICS = 3
PART1_QUESTIONS = (3, 4)      # each topic contributes three or four questions
PART3_BANK_QUESTIONS = 6      # plus one AI follow-up = the seven the spec requires

# §4.2 — Mock Test is timed, Practice Mode is not.
MOCK_LIMITS = {'part1': 40, 'part2': 120, 'part2_followup': 40, 'part3': 50}
PART2_PREP_SEC = 60
# Cả hai chế độ đều 10 giây. Tài liệu gốc ghi 3 giây cho Mock Test, nhưng chạy thử thấy
# quá gấp — thí sinh chưa kịp nghĩ đã bị chuyển câu (feedback 31/08).
SILENCE_SEC = {'mock': 10, 'practice': 10}


class NoMaterial(Exception):
    """The bank cannot produce this paper at all — not merely a narrowed filter."""


# ── Topic pools ────────────────────────────────────────────────────────────────────────

def _base_pool(db: Session, part: str, forecast: bool):
    """`is_active` chỉ có nghĩa "còn hiện trong Forecast hay không" — KHÔNG phải "còn được
    dùng để thi hay không". Một topic đã hết cửa sổ xuất hiện vẫn là đề hợp lệ: học viên
    không tick Forecast thì hệ thống tổ hợp từ TOÀN BỘ ngân hàng đề.

    Vì vậy cờ này chỉ được lọc khi học viên chọn luyện theo Forecast.
    """
    q = db.query(SpeakingTopic).filter(SpeakingTopic.part == part)
    return q.filter(SpeakingTopic.is_active.is_(True)) if forecast else q


def _in_forecast_month(query, month: Optional[date]):
    """§4.1: with "Luyện tập theo Forecast" ticked, only topics whose appearance window
    covers the chosen exam month qualify."""
    if not month:
        return query
    return query.filter(SpeakingTopic.appear_from.isnot(None),
                        SpeakingTopic.appear_to.isnot(None),
                        SpeakingTopic.appear_from <= month,
                        SpeakingTopic.appear_to >= month)


def _attempt_counts(db: Session, user_id: int) -> dict:
    """How many times this student has been asked each topic, across all their attempts.

    Drives "Ưu tiên đề đã làm / chưa làm" (§4.1) and, inside the done group, the
    "fewest times first" tie-break.
    """
    rows = (db.query(SpeakingAttemptAnswer.topic_id,
                     func.count(func.distinct(SpeakingAttemptAnswer.attempt_id)))
            .join(SpeakingAttempt,
                  SpeakingAttempt.attempt_id == SpeakingAttemptAnswer.attempt_id)
            .filter(SpeakingAttempt.user_id == user_id,
                    SpeakingAttemptAnswer.topic_id.isnot(None))
            .group_by(SpeakingAttemptAnswer.topic_id).all())
    return {tid: n for tid, n in rows}


def _order_by_priority(topics: List[SpeakingTopic], counts: dict, priority: str):
    """Sort the candidate topics so the preferred ones come first, without ever dropping
    the rest — a preference that empties the pool would break the "never fail" rule."""
    seen = [t for t in topics if counts.get(t.topic_id, 0) > 0]
    unseen = [t for t in topics if counts.get(t.topic_id, 0) == 0]
    random.shuffle(seen)
    random.shuffle(unseen)
    if priority == 'done':
        # Inside the done group the least-practised topic comes first (§4.1).
        seen.sort(key=lambda t: counts.get(t.topic_id, 0))
        return seen + unseen
    if priority == 'undone':
        seen.sort(key=lambda t: counts.get(t.topic_id, 0))
        return unseen + seen
    everything = seen + unseen
    random.shuffle(everything)
    return everything


def _pick_topics(db: Session, part: str, *, count: int, user_id: int, priority: str,
                 forecast_month: Optional[date] = None,
                 important: Optional[bool] = None,
                 exclude_work_study: Optional[str] = None,
                 exclude_ids: tuple = (),
                 required: bool = True) -> List[SpeakingTopic]:
    """Pick `count` topics, relaxing the soft filters one at a time rather than returning
    fewer than asked.

    `important` is a hard preference that gets dropped first: Topic 1 must be an important
    topic and Topics 2-3 must not be, but a bank where every topic is marked important
    still has to produce a paper. The work/study exclusion is dropped after it, because
    asking a working candidate about their studies is worse than repeating a topic.

    The forecast month is **never** relaxed: a student who ticked "Luyện tập theo
    Forecast" and chose a month asked for that month's material specifically, and §4.1
    answers an empty month with a message rather than with a different paper.

    `required=False` lets the caller take however many topics exist instead of failing —
    a Part 1 with two topics is a worse test than one with three, but it is still a test.
    """
    def fetch(*, with_important, with_work_study):
        q = _base_pool(db, part, forecast=bool(forecast_month))
        if exclude_ids:
            q = q.filter(~SpeakingTopic.topic_id.in_(exclude_ids))
        if with_important and important is not None:
            q = q.filter(SpeakingTopic.is_important.is_(important))
        if with_work_study and exclude_work_study:
            q = q.filter(SpeakingTopic.work_study != exclude_work_study)
        q = _in_forecast_month(q, forecast_month)
        return q.all()

    counts = _attempt_counts(db, user_id)
    picked: List[SpeakingTopic] = []
    chosen_ids = set(exclude_ids)
    for with_important, with_work_study in ((True, True), (True, False), (False, False)):
        pool = [t for t in fetch(with_important=with_important,
                                 with_work_study=with_work_study)
                if t.topic_id not in chosen_ids]
        for topic in _order_by_priority(pool, counts, priority):
            if len(picked) >= count:
                break
            picked.append(topic)
            chosen_ids.add(topic.topic_id)
        if len(picked) >= count:
            break
    if not picked and required:
        raise NoMaterial(f"There are no {part} topics in the question bank yet.")
    return picked


def _questions(db: Session, topic_id: int, part: str) -> List[SpeakingQuestion]:
    """Bank order, never shuffled (§4.3)."""
    return (db.query(SpeakingQuestion)
            .filter(SpeakingQuestion.topic_id == topic_id, SpeakingQuestion.part == part)
            .order_by(SpeakingQuestion.order_index, SpeakingQuestion.question_id).all())


# ── Steps ──────────────────────────────────────────────────────────────────────────────

def _script_step(name: str) -> dict:
    return {'kind': 'script', 'key': S.key_for_script(name), 'text': S.SCRIPTS[name]}


def _limit(part: str, mode: str) -> Optional[int]:
    return MOCK_LIMITS[part] if mode == 'mock' else None


def _question_step(q: SpeakingQuestion, topic: SpeakingTopic, mode: str, order: int) -> dict:
    return {
        'kind': 'question',
        'part': q.part,
        'question_id': q.question_id,
        'topic_id': topic.topic_id,
        'topic_title': topic.title,
        'text': q.content,
        'audio_key': S.key_for_question(q.question_id),
        'limit_sec': _limit(q.part, mode),
        'order_index': order,
    }


def _part1_steps(db, topics, mode, start_order) -> List[dict]:
    steps, order = [], start_order
    for topic in topics:
        qs = _questions(db, topic.topic_id, 'part1')
        if not qs:
            continue
        steps.append({'kind': 'script', 'key': S.key_for_topic_intro(topic.topic_id),
                      'text': S.topic_intro(topic.title), 'topic_id': topic.topic_id})
        take = random.choice(PART1_QUESTIONS)
        for q in qs[:take]:
            steps.append(_question_step(q, topic, mode, order))
            order += 1
    return steps


def _part2_steps(db, topic, mode, start_order) -> List[dict]:
    """The long turn plus its follow-up. The long turn is one step, not five: the order of
    intro → cue card → note minute → countdown → start line → speak → "Thank you." is
    fixed by the spec, so the room should not have to reassemble it from loose pieces.
    """
    long_turn = _questions(db, topic.topic_id, 'part2')
    if not long_turn:
        raise NoMaterial("This Part 2 topic has no cue card yet.")
    q = long_turn[0]
    steps = [_script_step('part2_intro_1'), _script_step('part2_intro_2'), {
        'kind': 'cue_card',
        'part': 'part2',
        'question_id': q.question_id,
        'topic_id': topic.topic_id,
        'topic_title': topic.title,
        'text': topic.cue_card or q.content,
        'audio_key': S.key_for_question(q.question_id),
        'prep_sec': PART2_PREP_SEC,
        'countdown_keys': [S.key_for_script(f'countdown_{n}') for n in (1, 2, 3)],
        'start_key': S.key_for_script('part2_start'),
        'stop_key': S.key_for_script('part2_stop'),
        'limit_sec': _limit('part2', mode),
        'order_index': start_order,
    }]
    order = start_order + 1

    # §4.3: exactly one follow-up, asked straight after the long turn.
    followups = _questions(db, topic.topic_id, 'part2_followup')
    if followups:
        steps.append(_question_step(random.choice(followups), topic, mode, order))
    return steps


def _part3_steps(db, topic, mode, start_order) -> List[dict]:
    steps = [{'kind': 'script', 'key': S.key_for_part3_intro(topic.topic_id),
              'text': S.part3_intro(topic.title), 'topic_id': topic.topic_id}]
    order = start_order
    for q in _questions(db, topic.topic_id, 'part3')[:PART3_BANK_QUESTIONS]:
        steps.append(_question_step(q, topic, mode, order))
        order += 1
    # The seventh question is written mid-test from what the student actually said, so it
    # carries no bank id and no clip — both are filled in when it is generated.
    steps.append({
        'kind': 'ai_followup',
        'part': 'part3',
        'question_id': None,
        'topic_id': topic.topic_id,
        'topic_title': topic.title,
        'text': None,
        'audio_key': None,
        'limit_sec': _limit('part3', mode),
        'order_index': order,
    })
    return steps


ASKED_KINDS = ('question', 'cue_card', 'ai_followup')


def _asked(steps: List[dict]) -> int:
    """order_index counts only the steps the student answers, so it lines up with the
    question numbering shown in the room and stored on each answer row."""
    return sum(1 for s in steps if s['kind'] in ASKED_KINDS)


# ── Entry point ────────────────────────────────────────────────────────────────────────

def build_plan(db: Session, *, user_id: int, test_type: str, mode: str,
               occupation: Optional[str] = None, voice: Optional[str] = None,
               forecast_month: Optional[date] = None,
               priority: str = 'default') -> dict:
    """Assemble one paper. Raises NoMaterial only when the bank cannot supply this part."""
    voice = voice or default_voice()
    # "Đang đi học" must not be handed the Work topic as their important topic, and the
    # other way round (§4.1). Neutral topics are fine for everyone.
    exclude = {'student': 'work', 'working': 'study'}.get(occupation or '')
    steps: List[dict] = []
    part2_topic = None

    if test_type in ('full', 'part1'):
        first = _pick_topics(db, 'part1', count=1, user_id=user_id, priority=priority,
                             forecast_month=forecast_month, important=True,
                             exclude_work_study=exclude)
        # The exclusion applies to every Part 1 topic, not just the important one: a real
        # examiner asks about work or about study, never both, so a working candidate must
        # not meet the Study topic in slot 2 either.
        # Đúng một topic important trong cả Part 1: hai topic còn lại phải là topic
        # thường. Không bắt buộc, vì ngân hàng chỉ có topic important thì vẫn phải thi
        # được (§4.1).
        rest = _pick_topics(db, 'part1', count=PART1_TOPICS - 1, user_id=user_id,
                            priority=priority, forecast_month=forecast_month,
                            important=False, exclude_work_study=exclude, required=False,
                            exclude_ids=tuple(t.topic_id for t in first))
        steps.append(_script_step('opening'))
        steps += _part1_steps(db, first + rest, mode, _asked(steps))

    if test_type in ('full', 'part2', 'part3'):
        part2_topic = _pick_topics(db, 'part2', count=1, user_id=user_id, priority=priority,
                                   forecast_month=forecast_month)[0]

    if test_type in ('full', 'part2'):
        steps += _part2_steps(db, part2_topic, mode, _asked(steps))

    if test_type in ('full', 'part3'):
        steps += _part3_steps(db, part2_topic, mode, _asked(steps))

    steps.append(_script_step('closing'))

    asked = [s for s in steps if s['kind'] in ASKED_KINDS]
    if not asked:
        raise NoMaterial("The question bank does not have enough material to build a test yet.")

    return {
        'test_type': test_type,
        'mode': mode,
        'voice': voice,
        'occupation': occupation,
        'forecast_month': forecast_month.isoformat() if forecast_month else None,
        'priority': priority,
        'silence_sec': SILENCE_SEC[mode],
        'part2_topic_id': part2_topic.topic_id if part2_topic else None,
        'question_count': len(asked),
        'steps': steps,
    }


# ── Forecast Parts (§7) ────────────────────────────────────────────────────────────────
#
# A deliberately different entry point from build_plan, because §7 removes the two things
# that make a Full Test a test: there is no assembly (the student picked the topic) and no
# mock mode (it is practice only). What replaces them is a promise — the room asks exactly
# the questions the student was shown before pressing Start. build_plan takes a random
# five of a topic's questions; doing that here would turn that preview into a lie.

def topic_questions(db: Session, topic: SpeakingTopic) -> List[SpeakingQuestion]:
    """Every question of a topic, in the order it will be asked.

    Part 2 is the long turn followed by its follow-ups; Part 3 hangs off the same topic
    row, so which questions belong to a "Part 3 topic" is decided by the part filter here
    rather than by a separate topic.
    """
    if topic.part == 'part1':
        return _questions(db, topic.topic_id, 'part1')
    return (_questions(db, topic.topic_id, 'part2')
            + _questions(db, topic.topic_id, 'part2_followup'))


def part3_questions(db: Session, topic: SpeakingTopic) -> List[SpeakingQuestion]:
    return _questions(db, topic.topic_id, 'part3')


def build_topic_plan(db: Session, *, topic: SpeakingTopic, section: str,
                     question_id: Optional[int] = None,
                     voice: Optional[str] = None) -> dict:
    """One topic's worth of practice (§7). `section` is 'part1' | 'part2' | 'part3'.

    Passing `question_id` narrows it to that single question — §7 lets a student drill one
    question without sitting through the whole topic. The examiner still introduces the
    topic first, because a question read with no lead-in is not what the exam sounds like.
    """
    voice = voice or default_voice()
    mode = 'practice'                      # §7: "Không có mock test, chỉ luyện tập."
    steps: List[dict] = []

    if section == 'part3':
        pool = part3_questions(db, topic)
        intro = {'kind': 'script', 'key': S.key_for_part3_intro(topic.topic_id),
                 'text': S.part3_intro(topic.title), 'topic_id': topic.topic_id}
    elif section == 'part1':
        pool = topic_questions(db, topic)
        intro = {'kind': 'script', 'key': S.key_for_topic_intro(topic.topic_id),
                 'text': S.topic_intro(topic.title), 'topic_id': topic.topic_id}
    else:
        pool = topic_questions(db, topic)
        intro = None

    if question_id is not None:
        pool = [q for q in pool if q.question_id == question_id]
        if not pool:
            raise NoMaterial("This question does not belong to this topic.")

    if not pool:
        raise NoMaterial("This topic has no questions yet.")

    if intro is not None:
        steps.append(intro)

    order = 0
    for q in pool:
        if q.part == 'part2':
            # The long turn keeps its whole ceremony — note minute, start line, "Thank
            # you." — otherwise practising Part 2 rehearses the wrong thing.
            steps += [_script_step('part2_intro_1'), _script_step('part2_intro_2'), {
                'kind': 'cue_card',
                'part': 'part2',
                'question_id': q.question_id,
                'topic_id': topic.topic_id,
                'topic_title': topic.title,
                'text': topic.cue_card or q.content,
                'audio_key': S.key_for_question(q.question_id),
                'prep_sec': PART2_PREP_SEC,
                'countdown_keys': [S.key_for_script(f'countdown_{n}') for n in (1, 2, 3)],
                'start_key': S.key_for_script('part2_start'),
                'stop_key': S.key_for_script('part2_stop'),
                'limit_sec': _limit('part2', mode),
                'order_index': order,
            }]
        else:
            steps.append(_question_step(q, topic, mode, order))
        order += 1

    steps.append(_script_step('closing'))
    asked = [s for s in steps if s['kind'] in ASKED_KINDS]

    return {
        'test_type': section,
        'mode': mode,
        'voice': voice,
        'occupation': None,
        'forecast_month': None,
        'priority': 'default',
        'silence_sec': SILENCE_SEC[mode],
        'part2_topic_id': topic.topic_id if topic.part == 'part2' else None,
        'question_count': len(asked),
        # Lets the result screen and the history list say which topic was drilled — a
        # Full Test has no single topic, so this field is what tells the two apart.
        'forecast_topic_id': topic.topic_id,
        'forecast_topic_title': topic.title,
        'steps': steps,
    }
