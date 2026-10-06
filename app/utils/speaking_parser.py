"""Turn pasted Speaking material into the rows the bank stores.

Admins add topics by pasting a block of text rather than filling a form per question —
see docs/speaking-spec.md §2. Parsing is deliberately dumb and predictable so what the
preview shows is exactly what gets saved:

  Part 1        first line = topic, every later line = one question
  Part 2        first line = topic, the whole block (that line included) = prompt + cue card
  Follow-up     one line = one question, always tied to the Part 2 topic
  Part 3        one line = one question, titled after the Part 2 topic
"""
import re


def _lines(text: str):
    """Non-empty lines, with list markers and stray numbering stripped."""
    out = []
    for raw in (text or '').replace('\r\n', '\n').split('\n'):
        line = raw.strip()
        if not line:
            continue
        # "1." / "1)" / "-" / "•" at the start is formatting, not content
        line = re.sub(r'^[\-•\*]\s*', '', line)
        line = re.sub(r'^\d+\s*[\.\)]\s+', '', line)
        line = line.strip()
        if line:
            out.append(line)
    return out


def parse_part1(text: str) -> dict:
    """{'title': str, 'questions': [str]} — first line names the topic."""
    lines = _lines(text)
    if not lines:
        return {'title': '', 'questions': []}
    return {'title': lines[0], 'questions': lines[1:]}


def parse_part2(text: str) -> dict:
    """{'title': str, 'cue_card': str} — the cue card keeps the topic line as written.

    The spec is explicit that the whole block, first line included, is the prompt shown
    to the student; the first line doubles as the topic name.
    """
    lines = _lines(text)
    if not lines:
        return {'title': '', 'cue_card': ''}
    return {'title': lines[0], 'cue_card': '\n'.join(lines)}


def parse_questions(text: str) -> list:
    """One line = one question. Used for follow-ups and for Part 3."""
    return _lines(text)


def parse_bundle(part1_text=None, part2_text=None, followup_text=None, part3_text=None) -> dict:
    """Preview payload for whichever boxes the admin filled in.

    Part 3 is stored against the Part 2 topic but is reported under its own heading so
    the preview shows the structure that will actually be saved.
    """
    preview = {}
    if part1_text:
        preview['part1'] = parse_part1(part1_text)
    if part2_text:
        part2 = parse_part2(part2_text)
        part2['followups'] = parse_questions(followup_text)
        part3 = parse_questions(part3_text)
        part2['part3'] = {
            'title': f"Part 3 - {part2['title']}" if part2['title'] else 'Part 3',
            'questions': part3,
        }
        preview['part2'] = part2
    return preview
