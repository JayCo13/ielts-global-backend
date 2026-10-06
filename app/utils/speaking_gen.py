"""Generate the "Gợi ý" and "Từ vựng chủ đề" for one Speaking question.

Global port: everything the student reads is English — the outline labels, the model
answers, and the vocabulary "meaning" (stored in the legacy `meaning_vi` column, which now
holds a short English definition). Examples are no longer tied to Vietnamese life.

docs/speaking-spec.md §2.3. Two independent blocks per question:

  A. an outline plus four band-level model answers — the outline's shape differs per
     part, so it is stored as JSON rather than columns;
  B. topic vocabulary, up to 6 items at each of 4 bands.

Both run from a background job because one Part 1 topic is ~10 questions = 10 outlines
and 10 vocabulary sets; nobody is going to sit and watch that finish.

The rule that matters most, and the one a model will quietly break: the four bands
differ in **Vocabulary, Grammar, Fluency and Naturalness — not length or difficulty**.
A band 8 answer is more natural and more precise than a band 5 one, not longer or more
elaborate. Every prompt below states it, and the length limits are given per band so
the model cannot express "higher band" as "more words".
"""
import re

from app.utils.gemini_client import generate_json, MODEL

BANDS = ('4.5-5.5', '6.0-6.5', '7.0-7.5', '8.0-9.0')

# Banned because they turn a spoken answer into a written one. The documents call this
# out repeatedly; it is the single most common tell of an AI-written model answer.
_COMMON = """You are an experienced IELTS Speaking examiner and tutor writing study
material for international IELTS learners. Write everything in English.

Hard rules, all of them:
- The four bands differ across Vocabulary, Grammar, Sentence Structure, Development,
  Precision AND Naturalness together — not by swapping in harder words. A higher band is
  more natural and more precise, NEVER longer and never more complicated for its own
  sake. Respect the length limit for every band.
- The ideas themselves stay identical across all four bands. Only the language changes.
  Never rewrite the supporting ideas to make a higher band look better.
- Write speech, not writing — an IELTS Speaking answer, not a Writing Task 2 paragraph.
- Do not lean on these: "I think", "One reason is that", "Another reason is that",
  "For example", "As a result", "Firstly", "Secondly", "In conclusion", "Overall",
  "Moreover", "Furthermore". Connectors are fine when they come out naturally; what is
  banned is the mechanical repetition. Natural flow beats linking words.
- No idiom-stuffing. No advanced vocabulary reached for just to look like a higher band.
  No sentence that is too long or too academic to say out loud.
- Naturalness beats formula. If a rule would make the answer sound stiff, break it.
- Plain, concrete detail beats abstract vocabulary.
- Return strict JSON only, no markdown fences, no commentary.
"""

_OUTLINE_SHAPE = {
    'part1': """Outline shape for Part 1:
{
  "question_type": "<what kind of Part 1 question this is, in English>",
  "direct_answer": "<the one-sentence direct answer, in English>",
  "explanation": ["<3 to 4 short supporting ideas, English, a few words each>"]
}""",
    'part2': """Outline shape for Part 2 (the long turn):
Identify EXACTLY 4 cue cards from the prompt — one per bullet the candidate must cover.
{
  "cue_cards": [
    {
      "cue": "<the cue card this paragraph answers>",
      "main_point": "<one sentence>",
      "development": ["<detail 1>", "<detail 2>"],
      "specific_detail": "<one concrete, specific detail: a name, a number, a place>"
    }
  ]
}
The 4th entry additionally carries "feeling", "result" and "reflection" keys.""",
    'part3': """Outline shape for Part 3:
{
  "question_type": "<Opinion | Reasons | Advantages/Disadvantages | Effects | Problems/Solutions | Importance | Comparison | Past/Present | Future | Changes | Preference | Yes/No | How/Ways | or whatever genuinely fits — do not force the question into a template>",
  "direct_answer": "<answers the question, but ONLY in general terms — see the rule below>",
  "idea_1": {"idea": "...", "example": "...", "result": "..."},
  "idea_2": {"idea": "...", "example": "...", "result": "..."}
}

THE DIRECT ANSWER RULE — the one that matters most:
Answer the question clearly, but do NOT give away the supporting ideas.
The direct answer states the position or trend in general terms and leaves the two
specific reasons for the sentences that follow. It must not list, name or hint at them.

  Question: What factors make a place suitable for living?
  WRONG: "A place is suitable for living because it has good transport and affordable
         housing."  <- both supporting ideas already spent
  RIGHT: "A place can be suitable for living for several different reasons."

Keep it short, easy to say and natural. Do not default to "I think" or "In my opinion".
Openers like "Yes, generally speaking...", "No, not necessarily...", "This can vary from
person to person...", "A number of factors can influence this..." are available when they
genuinely fit — naturalness comes first.

SUPPORTING IDEAS:
- Exactly two, clearly different from each other, each answering the question directly.
- Each example must be concrete and tied to its own idea — never a generic one.
- Each result must follow logically from its idea or example.
- Reject empty ideas such as "It is beneficial", "It is important", "It has many
  advantages", "It is good for people".
- Examples may draw on students, families, schools, workplaces or everyday life
  where that fits.
- The outline itself is keywords and short phrases, never written-out sentences: a
  learner should be able to glance at it and speak for 30-40 seconds.""",
}

_SAMPLE_SHAPE = {
    'part1': "4-5 sentences, speakable in 15-25 seconds.",
    'part2': ("EXACTLY 4 paragraphs, one per cue card, separated by a blank line. "
              "TOTAL LENGTH must land between 200 and 240 words — aim for about 225, "
              "which is roughly 56 words in each of the four paragraphs. Count them "
              "before you answer: both 190 and 250 are wrong, however well they read. "
              "Speakable in 1:30-2:00."),
    'part3': ("EXACTLY 7 sentences, in this order and no other: direct answer, "
              "supporting idea 1, example 1, result 1, supporting idea 2, example 2, "
              "result 2. This is the logic of the development, not a formula — the same "
              "linking words need not appear in every band. Speakable in 30-50 seconds. "
              "No opening line, no conclusion, and never an eighth sentence. The first "
              "sentence must stay general and must not reveal either supporting idea."),
}

# A follow-up is asked straight after the long turn and answered briefly, like Part 1.
_SHAPE_FOR = {'part1': 'part1', 'part2': 'part2', 'part2_followup': 'part1', 'part3': 'part3'}


def _shape(part: str) -> str:
    return _SHAPE_FOR.get(part, 'part1')


def build_suggestion_prompt(part: str, question: str, topic_title: str = ''):
    shape = _shape(part)
    system = _COMMON + "\n" + _OUTLINE_SHAPE[shape]
    user = f"""Topic: {topic_title or '(none)'}

Question / prompt:
{question}

Produce JSON with exactly two keys:
{{
  "outline": <the outline object described above>,
  "samples": {{
    "4.5-5.5": "<model answer>",
    "6.0-6.5": "<model answer>",
    "7.0-7.5": "<model answer>",
    "8.0-9.0": "<model answer>"
  }}
}}

Every model answer must obey: {_SAMPLE_SHAPE[shape]}

The four levels, as they differ in practice:
  4.5-5.5  basic vocabulary, fairly simple sentences, grammar need not vary much, easy
           to say and to imitate — but the ideas are still clear and logical.
  6.0-6.5  wider vocabulary, natural collocations, some moderately complex sentences,
           safe but varied grammar, ideas developed more fully than the band below.
  7.0-7.5  flexible and accurate vocabulary, good collocations, varied grammar and
           sentence structure, coherent natural development, able to go deeper without
           turning academic.
  8.0-9.0  precise, natural, well-judged word choice, flexible grammar, varied structure,
           deep but concise development, nuance where it fits — a fluent speaker who is
           not showing off.

All four answer the SAME question with the SAME ideas — only the language level changes.

Before you answer, check: is the first sentence still general, or has it already named
one of the supporting ideas? If it has, rewrite it more generally.
"""
    return system, user


def build_vocabulary_prompt(part: str, question: str, topic_title: str = ''):
    system = _COMMON + """
You are choosing vocabulary a learner can use to answer ONE specific question.

- Aim for 6 items per band level; 4 levels; 24 items maximum.
- Prefer chunks and collocations over single words.
- Never repeat an item across levels.
- Return fewer than 6 only when you genuinely cannot find another item that a learner
  could use directly in this answer. Two or three items is almost always you giving up
  too early — for most questions six usable chunks exist at every level.
- Reject anything that is merely "about the topic". Each item must be usable
  DIRECTLY in an answer to this exact question.
"""
    user = f"""Topic: {topic_title or '(none)'}

Question / prompt:
{question}

Return JSON:
{{
  "4.5-5.5": [{{"term": "...", "meaning_vi": "<a short, simple English definition (the key name is legacy — write English)>", "example": "<one natural English sentence>"}}],
  "6.0-6.5": [...],
  "7.0-7.5": [...],
  "8.0-9.0": [...]
}}
"""
    return system, user


def _paragraphs(text: str):
    return [p for p in (text or '').split('\n\n') if p.strip()]


def _sentences(text: str):
    return [x for x in re.split(r'(?<=[.!?])\s+', (text or '').strip()) if x.strip()]


BANNED = ('firstly', 'secondly', 'in conclusion', 'overall', 'moreover', 'furthermore')

# These are ordinary spoken English — "I think" is what people actually say. The spec
# bans *mechanical repetition* of them, not the phrases themselves, so they are only
# flagged when the same opener starts two or more sentences in one answer. Banning them
# outright would fight natural speech and burn a retry on every generation, the same way
# matching "overall" everywhere did before it was narrowed to sentence-initial.
CRUTCHES = ('i think', 'one reason is that', 'another reason is that',
            'for example', 'as a result')
_CRUTCH_RE = {
    c: re.compile(r'(?:^|[.!?]["\')\]]?\s+|\n\s*)' + c.replace(' ', r'\s+') + r'\b', re.I)
    for c in CRUTCHES
}
# Only at the start of a sentence — that is where these act as the essay-style
# discourse markers the spec bans. Matching them anywhere flagged perfectly natural
# lines like "the overall buzz of the city", and each false positive burned a retry.
_BANNED_RE = re.compile(
    r'(?:^|[.!?]["\')\]]?\s+|\n\s*)(' + '|'.join(BANNED) + r')\b', re.I)


def _problems(shape: str, samples: dict):
    """Complaints about the generated answers, as (hard, message, penalty) triples.

    Telling the model a rule once is not enough — the first Part 2 run came back at
    162-168 words against a 200-240 requirement, and a banned connector still slipped
    into one band-8 answer. Feeding the specific miss back is what actually fixes it.
    "Hard" problems are the ones the spec states as exact counts, so they are worth
    failing over; length and word choice are near-misses that still leave usable
    material, so they only trigger the retry.
    """
    out = []
    for band in BANDS:
        text = samples.get(band) or ''
        words = len(text.split())
        crutches = sorted(c for c, rx in _CRUTCH_RE.items() if len(rx.findall(text)) >= 2)
        if crutches:
            out.append((False, f'band {band}: mechanically repeats "{crutches[0]}" at the start '
                               f'of several sentences — vary the linking so it sounds natural',
                        6 * len(crutches)))

        banned = sorted({m.group(1).lower() for m in _BANNED_RE.finditer(text)})
        if banned:
            out.append((False, f'band {band}: uses banned connectors ({", ".join(banned)}) — replace them with natural spoken linking',
                        10 * len(banned)))
        if shape == 'part2':
            paras = len(_paragraphs(text))
            if paras != 4:
                out.append((True, f'band {band}: has {paras} paragraphs, must have exactly 4', 1000))
            if words > 240:
                out.append((False, f'band {band}: {words} words — TOO LONG, cut it to 215-235 words', words - 240))
            elif words < 200:
                out.append((False, f'band {band}: {words} words — TOO SHORT, extend it to 215-235 words', 200 - words))
        elif shape == 'part3':
            n = len(_sentences(text))
            if n != 7:
                out.append((True, f'band {band}: has {n} sentences, must have exactly 7', 1000))
        else:
            n = len(_sentences(text))
            if not (4 <= n <= 5):
                out.append((False, f'band {band}: has {n} sentences, should have 4-5',
                            abs(n - 4 if n < 4 else n - 5)))
    return out


MAX_ATTEMPTS = 3


def generate_suggestion(part: str, question: str, topic_title: str = '') -> dict:
    """{'outline': ..., 'samples': {band: text}, 'model': ...} — raises on failure.

    Gemini will not reliably hit "200-240 words" from the prompt alone: three runs of
    the same Part 2 question came back at 272, 170 and 200 words. Telling it what it got
    wrong helps but still oscillates around the boundary, so rather than trusting the
    last attempt we take up to MAX_ATTEMPTS and keep the BEST one — otherwise a single
    bad roll ships a 170-word long turn that is half a minute short when spoken.
    """
    shape = _shape(part)
    system, user = build_suggestion_prompt(part, question, topic_title)

    best = None            # (score, outline, samples)
    complaints = []
    for attempt in range(MAX_ATTEMPTS):
        prompt = user
        if complaints:
            prompt += ("\n\nYour previous attempt broke these requirements:\n- "
                       + "\n- ".join(complaints)
                       + "\n\nRewrite all four answers so every one of them is fixed. Keep "
                         "the same ideas and the same content — correct only the structure, "
                         "the length and the word choice.")

        data = generate_json(system, prompt, temperature=0.6)
        outline, samples = data.get('outline'), data.get('samples') or {}

        # A missing band is unusable — no score, just try again.
        missing = [b for b in BANDS if not (samples.get(b) or '').strip()]
        if missing:
            complaints = [f'the band {b} model answer is missing entirely' for b in missing]
            continue

        problems = _problems(shape, samples)
        score = sum(p for _, _, p in problems)
        if best is None or score < best[0]:
            best = (score, outline, samples)
        if score == 0:
            break
        complaints = [m for _, m, _ in problems]

    if best is None:
        raise ValueError("could not generate model answers for all 4 bands")

    score, outline, samples = best
    hard = [m for is_hard, m, _ in _problems(shape, samples) if is_hard]
    if hard:
        raise ValueError('; '.join(hard))

    return {'outline': outline, 'samples': {b: samples[b].strip() for b in BANDS},
            'model': MODEL}


def generate_vocabulary(part: str, question: str, topic_title: str = '') -> list:
    """[(band, order_index, term, meaning_vi, example)] — capped at 6 per band."""
    system, user = build_vocabulary_prompt(part, question, topic_title)
    data = generate_json(system, user, temperature=0.5)
    rows = []
    seen = set()
    for band in BANDS:
        items = data.get(band) or []
        if not isinstance(items, list):
            continue
        order = 0
        for it in items[:6]:
            if not isinstance(it, dict):
                continue
            term = (it.get('term') or '').strip()
            if not term:
                continue
            key = term.lower()
            if key in seen:      # the prompt forbids repeats; enforce it anyway
                continue
            seen.add(key)
            rows.append((band, order, term[:255],
                         (it.get('meaning_vi') or '').strip()[:500] or None,
                         (it.get('example') or '').strip() or None))
            order += 1
    if not rows:
        raise ValueError("could not generate any vocabulary")
    return rows


def summarise(suggestion: dict, vocab_rows: list) -> str:
    """Short line for the job log."""
    per_band = {b: 0 for b in BANDS}
    for band, *_ in vocab_rows:
        per_band[band] = per_band.get(band, 0) + 1
    return "samples=%d, vocab=%d (%s)" % (
        len(suggestion.get('samples') or {}), len(vocab_rows),
        '/'.join(str(per_band[b]) for b in BANDS))
