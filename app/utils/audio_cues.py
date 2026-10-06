"""Turn a listening part's word-level alignment into per-question audio cues.

`listening_alignments` holds, for each part, the authored transcript's words alongside
the audio offset each was spoken at. A question's answer lives somewhere in that text —
either as the `locate` excerpt an admin wrote, or as the answer word itself — so finding
that text gives the moment to replay.

Two things keep this honest:

* IELTS questions always follow the recording's order, so each cue must start after the
  previous one. That single constraint resolves nearly all "this word appears three
  times" ambiguity, including the answer key and question sheet that sit above the
  transcript in the same field.
* Multiple-choice answers ("B") are not spoken, so they get no anchor of their own.
  They fall back to the stretch between their neighbours — a rough window, flagged as
  such, rather than a wrong-but-confident three seconds.
"""
import gzip
import html
import json
import re
from collections import Counter

from app.models.models import ListeningAlignment, ListeningCueOverride, Question

LEAD_IN = 1.5      # seconds of run-up so the answer isn't the very first thing heard
TAIL = 1.2         # seconds after the last matched word
MIN_LEN = 2.5      # never hand back a snippet too short to make sense of
MAX_RANGE = 60.0   # a bracketed guess wider than this is worse than offering nothing
MCQ = re.compile(r'^[a-h]$', re.I)
PLACEHOLDER = re.compile(r'^(question|câu)\s*\d+$', re.I)


def _clean(s):
    s = re.sub(r'<[^>]+>', ' ', s or '')
    return re.sub(r'\s+', ' ', html.unescape(s).replace('\xa0', ' ')).strip()


def _key(w):
    return re.sub(r"[^a-z0-9']", '', w.lower())


def _phrase(text):
    return [k for k in (_key(w) for w in _clean(text).split(' ')) if k]


def _occurrences(hay, needle):
    n = len(needle)
    if not n:
        return []
    return [i for i in range(len(hay) - n + 1) if hay[i:i + n] == needle]


MIN_RUN = 4


def _locate_matches(hay, needle):
    """Where a phrase sits in the transcript, matching as leniently as the highlight does.

    The student-facing highlight (ielts-tajun listening main_layout) falls back to the
    longest contiguous run of >= 4 words when the full phrase has drifted from the
    transcript — an admin retypes an excerpt and drops a word, and exact matching misses
    it. Cues have to accept exactly what the highlight accepts, otherwise a question can
    light up in the transcript yet refuse to jump the audio.

    Returns [(index, length)], longest run first.
    """
    exact = _occurrences(hay, needle)
    if exact:
        return [(i, len(needle)) for i in exact]
    for length in range(len(needle) - 1, MIN_RUN - 1, -1):
        for start in range(0, len(needle) - length + 1):
            hits = _occurrences(hay, needle[start:start + length])
            if hits:
                return [(i, length) for i in hits]
    return []


SENTENCE_END = re.compile(r'[.!?]["\')\]]?$')
# Some transcripts carry an inline answer key: "... in theatre (👉 Question 1 – theatre)".
# Useful for a human reading the transcript, noise inside a locate excerpt.
ANNOTATION = re.compile(r'\(\s*\U0001F449[^)]*\)')
MAX_SENTENCE = 30


def _sentence_around(raw_tokens, start, length):
    """Widen a match to the sentence holding it.

    Highlighting a single answer word ("theatre") reads as a glitch next to the old
    behaviour, which lit up a whole passage. Walk out to sentence boundaries, capped so
    a transcript without punctuation can't select half the page.
    """
    lo = start
    while lo > 0 and start - lo < MAX_SENTENCE // 2:
        if SENTENCE_END.search(raw_tokens[lo - 1]):
            break
        lo -= 1
    hi = start + length
    while hi < len(raw_tokens) and hi - start < MAX_SENTENCE:
        if SENTENCE_END.search(raw_tokens[hi - 1]):
            break
        hi += 1
    return ' '.join(raw_tokens[lo:hi])


def build_cues(db, section_id):
    """[{question_id, question_number, start, end, source}] for one listening part."""
    alignment = db.query(ListeningAlignment).filter(
        ListeningAlignment.section_id == section_id).first()
    if not alignment or not alignment.data_gz:
        return []

    data = json.loads(gzip.decompress(alignment.data_gz).decode('utf-8'))
    raw_tokens = data['tokens']            # as written, for highlighting
    tokens = [_key(t) for t in raw_tokens]  # normalised, for matching
    times = data['times']
    timed = [i for i, t in enumerate(times) if t is not None]
    if not timed:
        return []
    first, last = timed[0], timed[-1]

    def time_at(i, forward=True):
        rng = range(i, min(i + 8, len(times))) if forward else range(i, max(i - 8, -1), -1)
        for j in rng:
            if times[j] is not None:
                return times[j]
        return None

    questions = (
        db.query(Question)
        .filter(Question.section_id == section_id, Question.question_type != 'main_text')
        .order_by(Question.question_id)
        .all()
    )

    locate_counts = Counter(
        _clean(q.locate or '') for q in questions if _clean(q.locate or ''))

    # An admin's own timestamp always wins — including for multiple choice, where no
    # automatic anchor is possible.
    overrides = {
        row.question_id: row for row in
        db.query(ListeningCueOverride).filter(
            ListeningCueOverride.section_id == section_id).all()
    }

    cues = []
    previous = -1.0
    for q in questions:
        pinned = overrides.get(q.question_id)
        if pinned:
            previous = float(pinned.start_time)
            cues.append({'question_id': q.question_id, 'question_number': q.question_number,
                         'start': round(float(pinned.start_time), 2),
                         'end': round(float(pinned.end_time), 2),
                         'source': 'manual', 'phrase': None})
            continue

        # Order matters. Measured against the "answers follow the recording" rule, a
        # locate excerpt lands in the right place 735/737 times; a bare answer word only
        # 978/1018, because a common word recurs elsewhere in the audio. So locate wins —
        # unless the same excerpt was pasted onto several questions in this part, which
        # makes it useless for telling them apart.
        candidates = []
        locate = _clean(q.locate or '')
        if locate and not PLACEHOLDER.match(locate) and locate_counts[locate] == 1:
            phrase = _phrase(locate)
            if phrase:
                candidates.append((phrase, 'locate'))
        answer = _clean(q.correct_answer or '')
        variants = [v.strip() for v in re.split(r'\bor\b|/|,', answer) if v.strip()]
        for v in variants:
            if not MCQ.match(v):
                phrase = _phrase(v)
                if phrase:
                    candidates.append((phrase, 'answer'))

        hit = source = None
        for phrase, kind in candidates:
            # Only inside the spoken span — the answer key printed above the transcript
            # would otherwise win every time.
            found = [(i, ln) for i, ln in _locate_matches(tokens, phrase) if first <= i <= last]
            # Strictly forward: reusing an earlier position would just clone the previous
            # cue, which is worse than admitting we don't know.
            ahead = [(i, ln) for i, ln in found if (time_at(i) or -1) > previous]
            if ahead:
                (hit, matched_len), source = ahead[0], kind
                needle = phrase[:matched_len]
                break

        if hit is not None:
            start = time_at(hit)
            end = time_at(hit + len(needle) - 1, forward=False)
            if start is not None:
                end = max(end if end is not None else start, start) + TAIL
                start = max(0.0, start - LEAD_IN)
                if end - start < MIN_LEN:
                    end = start + MIN_LEN
                previous = start
                cues.append({
                    'question_id': q.question_id, 'question_number': q.question_number,
                    'start': round(start, 2), 'end': round(end, 2), 'source': source,
                    # The exact words matched, so the review screen can highlight the
                    # transcript even where `locate` is a placeholder like "question 31".
                    'phrase': _sentence_around(raw_tokens, hit, len(needle)),
                })
                continue

        cues.append({'question_id': q.question_id, 'question_number': q.question_number,
                     'start': None, 'end': None, 'source': 'none'})

    _fill_gaps(cues, float(alignment.audio_duration or 0))
    return cues


def _fill_gaps(cues, duration):
    """Give unanchored questions (multiple choice) the stretch between their neighbours.

    Not precise, and marked `range` so the UI can say so — but a 30-second window beats
    scrubbing a ten-minute recording by hand.
    """
    for idx, cue in enumerate(cues):
        if cue['start'] is not None:
            continue
        before = next((c for c in reversed(cues[:idx]) if c['start'] is not None), None)
        after = next((c for c in cues[idx + 1:] if c['start'] is not None), None)
        if not before or not after:
            continue      # an open-ended guess at the very start or end says nothing
        start, end = before['end'], after['start']
        if MIN_LEN <= end - start <= MAX_RANGE:
            cue.update({'start': round(start, 2), 'end': round(end, 2), 'source': 'range'})


def suggest_locates(db, section_id):
    """Propose a `locate` excerpt for each question of a listening part.

    The alignment already tells us where every transcript word is spoken, and for a
    gap-fill question the answer itself is a word that was spoken — so the sentence
    holding it is exactly the excerpt an admin would have typed by hand. Filling those
    in fixes two things at once: the transcript highlight, and the replay button.

    Multiple-choice answers ("B") are never spoken, so they get no suggestion — those
    still need a human. Suggestions run strictly forward through the recording, the same
    ordering rule build_cues() uses.
    """
    alignment = db.query(ListeningAlignment).filter(
        ListeningAlignment.section_id == section_id).first()
    if not alignment or not alignment.data_gz:
        return []

    data = json.loads(gzip.decompress(alignment.data_gz).decode('utf-8'))
    raw_tokens = data['tokens']
    tokens = [_key(t) for t in raw_tokens]
    times = data['times']
    timed = [i for i, t in enumerate(times) if t is not None]
    if not timed:
        return []
    first, last = timed[0], timed[-1]

    def time_at(i):
        for j in range(i, min(i + 8, len(times))):
            if times[j] is not None:
                return times[j]
        return None

    questions = (
        db.query(Question)
        .filter(Question.section_id == section_id, Question.question_type != 'main_text')
        .order_by(Question.question_id)
        .all()
    )

    out = []
    previous = -1.0
    for order, q in enumerate(questions, start=1):
        current = _clean(q.locate or '')
        row = {
            'question_id': q.question_id,
            'order': order,
            'current_locate': current,
            'is_placeholder': bool(current and PLACEHOLDER.match(current)),
            'is_empty': not current,
            'answer': _clean(q.correct_answer or ''),
            'suggestion': None,
            'start': None,
        }

        variants = [v.strip() for v in re.split(r'\bor\b|/|,', row['answer']) if v.strip()]
        for v in variants:
            if MCQ.match(v):
                continue
            phrase = _phrase(v)
            if not phrase:
                continue
            found = [i for i, _ in _locate_matches(tokens, phrase) if first <= i <= last]
            ahead = [i for i in found if (time_at(i) or -1) > previous]
            if not ahead:
                continue
            hit = ahead[0]
            start = time_at(hit)
            if start is None:
                continue
            previous = start
            sentence = _sentence_around(raw_tokens, hit, len(phrase))
            row['suggestion'] = re.sub(r'\s+', ' ', ANNOTATION.sub('', sentence)).strip()
            row['start'] = round(start, 2)
            break

        out.append(row)
    return out
