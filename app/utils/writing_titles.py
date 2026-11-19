"""Display title of a Writing task, taken automatically from its prompt (feedback
2026-11-14: Full Test Writing cards show a title per part like Listening / Reading,
"nhận tự động từ bài để đỡ nhập").

`instructions` can embed base64 images, so list endpoints defer it. Titles are therefore
cached per process keyed by (task_id, LENGTH(instructions)): one cheap length query per
request, and the heavy column is read only for tasks that are new or were edited.
"""
import html
import re
import threading
import time
from typing import Dict, Iterable

from sqlalchemy import func

from app.models.models import WritingTask

MAX_LEN = 120
_cache: Dict[int, tuple] = {}          # task_id -> (instructions length, title)
# The length check itself scans every LONGTEXT (base64 images) on TiDB, which made the
# Writing lists slow (2026-11-19). Known tasks are trusted for CHECK_EVERY seconds;
# only tasks never seen before are read in between. Admin edits show within that window.
CHECK_EVERY = 300
_checked_at: Dict[int, float] = {}    # task_id -> when its length was last checked
_lock = threading.Lock()

# A title the admin typed is kept only if it says more than "Task 1" / "Part 2".
_GENERIC = re.compile(r"^\s*(writing\s*)?(task|part)?\s*\d*\s*$", re.I)

# Standard exam boilerplate that is not the topic of the task.
_BOILERPLATE = [
    r"you should spend (about|no more than)?\s*\d+\s*minutes on this task\.?",
    r"write about the following topic\s*:?",
    r"write at least \d+\s*words\.?",
    r"summari[sz]e the information by selecting and reporting the main features,? and make comparisons where relevant\.?",
    r"give reasons for your answer and include any relevant examples from your own knowledge or experience\.?",
    r"you do not need to write any addresses\.?",
    r"begin your letter as follows\s*:?.*?$",
]
_BOILER_RE = re.compile("|".join(_BOILERPLATE), re.I | re.M)


def title_from_instructions(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = _BOILER_RE.sub(" ", text)
    text = re.sub(r"[ \t ]+", " ", text)
    lines = [ln.strip(" -•*:") for ln in text.split("\n")]
    text = " ".join(ln for ln in lines if len(ln) > 2)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    # First sentence carries the topic ("The chart below shows …", "Some people believe …").
    m = re.match(r"(.+?[.?!])(\s|$)", text)
    first = m.group(1) if m and len(m.group(1)) >= 25 else text
    if len(first) > MAX_LEN:
        cut = first[:MAX_LEN].rsplit(" ", 1)[0]
        first = cut.rstrip(",;:") + "…"
    return first


def part_titles(db, tasks: Iterable) -> Dict[int, str]:
    """{task_id: display title} for already-loaded WritingTask rows (instructions deferred)."""
    tasks = list(tasks)
    ids = [t.task_id for t in tasks]
    if not ids:
        return {}
    now = time.monotonic()
    check = [i for i in ids if i not in _cache or now - _checked_at.get(i, 0.0) >= CHECK_EVERY]
    if check:
        with _lock:
            lengths = dict(db.query(WritingTask.task_id, func.coalesce(func.length(WritingTask.instructions), 0))
                           .filter(WritingTask.task_id.in_(check)).all())
            stale = [i for i in check if _cache.get(i, (None,))[0] != lengths.get(i, 0)]
            if stale:
                for tid, ins in db.query(WritingTask.task_id, WritingTask.instructions).filter(WritingTask.task_id.in_(stale)).all():
                    _cache[tid] = (lengths.get(tid, 0), title_from_instructions(ins or ""))
            for i in check:
                _cache.setdefault(i, (lengths.get(i, 0), ""))
                _checked_at[i] = now
    out = {}
    for t in tasks:
        own = (t.title or "").strip()
        auto = _cache.get(t.task_id, (0, ""))[1]
        out[t.task_id] = own if own and not _GENERIC.match(own) else (auto or own)
    return out
