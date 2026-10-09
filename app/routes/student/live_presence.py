"""Live "N people are taking this test" counter (ported from VN).

A per-scope presence count, independent of the F7 exam heartbeat
(`exam_progress_routes.py`, which upserts a TiDB row per beat). Nothing here touches
the database: presence lives in Redis when it is available and in this process's
memory when it is not.

A "scope" is the presence bucket: the exam id for a full test ("53"), or
"{exam_id}p{part}" for a single focus part ("53p2").

Storage
  * Redis (preferred): one sorted set per scope, member = user id, score = epoch of
    the last heartbeat. Counting prunes members older than STALE_WINDOW and returns
    ZCARD. Keys self-expire, so an abandoned scope cleans itself up.
  * In-memory fallback: same shape in a module-level dict. Used when Redis is not
    connected or a Redis call fails. It is per process, so with more than one worker
    each worker only sees the takers whose heartbeats it received.

Only real takers are counted. The VN version adds a synthetic "social proof" number
on top of the real count; that part is deliberately not ported.
"""
import re
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.models.models import User
from app.routes.admin.auth import get_current_student
from app.utils.redis_cache import cache

router = APIRouter()

# A taker is "live" if they heartbeat within this many seconds. The exam rooms beat
# every 20s, so 60s tolerates two missed beats (background-tab timer throttling).
STALE_WINDOW = 60
# Hard caps: a list page asks for the cards it shows, never the whole catalogue.
MAX_BATCH_SCOPES = 60
MAX_MEMORY_SCOPES = 5000

_SCOPE_RE = re.compile(r"^[0-9]{1,10}(p[0-9]{1,2})?$")

# In-memory fallback: scope -> {user_id: last_beat_epoch}
_memory: Dict[str, Dict[str, float]] = {}


def _valid(scope: str) -> bool:
    return bool(scope) and bool(_SCOPE_RE.match(scope))


def _key(scope: str) -> str:
    return f"live_takers:{scope}"


# ── in-memory fallback ──────────────────────────────────────────────────────
def _mem_prune(scope: str, now: float) -> Dict[str, float]:
    members = _memory.get(scope)
    if not members:
        _memory.pop(scope, None)
        return {}
    cutoff = now - STALE_WINDOW
    stale = [uid for uid, ts in members.items() if ts < cutoff]
    for uid in stale:
        del members[uid]
    if not members:
        _memory.pop(scope, None)
        return {}
    return members


def _mem_beat(scope: str, user_id: str, now: float) -> None:
    if scope not in _memory and len(_memory) >= MAX_MEMORY_SCOPES:
        # Sweep every scope once before refusing to grow any further.
        for s in list(_memory.keys()):
            _mem_prune(s, now)
        if len(_memory) >= MAX_MEMORY_SCOPES:
            return
    _memory.setdefault(scope, {})[user_id] = now


def _mem_leave(scope: str, user_id: str) -> None:
    members = _memory.get(scope)
    if members:
        members.pop(user_id, None)
        if not members:
            _memory.pop(scope, None)


def _mem_count(scope: str, now: float) -> int:
    return len(_mem_prune(scope, now))


# ── storage-agnostic operations (never raise) ───────────────────────────────
async def _beat(scope: str, user_id: str) -> None:
    now = time.time()
    client = cache.redis_client
    if client:
        try:
            pipe = client.pipeline()
            pipe.zadd(_key(scope), {user_id: now})
            # Safety expiry so an abandoned set self-cleans.
            pipe.expire(_key(scope), STALE_WINDOW * 3)
            await pipe.execute()
            return
        except Exception:
            pass
    _mem_beat(scope, user_id, now)


async def _leave(scope: str, user_id: str) -> None:
    client = cache.redis_client
    if client:
        try:
            await client.zrem(_key(scope), user_id)
        except Exception:
            pass
    # Always clear the fallback too: the beat may have landed there during a Redis blip.
    _mem_leave(scope, user_id)


async def _counts(scopes: List[str]) -> Dict[str, int]:
    """Live count per scope. One Redis round trip for the whole batch."""
    if not scopes:
        return {}
    now = time.time()
    client = cache.redis_client
    if client:
        try:
            pipe = client.pipeline()
            for scope in scopes:
                # Drop stale members, then count what is left.
                pipe.zremrangebyscore(_key(scope), "-inf", now - STALE_WINDOW)
                pipe.zcard(_key(scope))
            res = await pipe.execute()
            return {scope: int(res[i * 2 + 1] or 0) for i, scope in enumerate(scopes)}
        except Exception:
            pass
    return {scope: _mem_count(scope, now) for scope in scopes}


async def _count(scope: str) -> int:
    return (await _counts([scope])).get(scope, 0)


# ── routes (mounted under /student) ─────────────────────────────────────────
@router.post("/live-presence/{scope}/heartbeat")
async def heartbeat(
    scope: str,
    current_user: User = Depends(get_current_student),
):
    """Mark the current student as active in `scope`. Returns the live count."""
    if not _valid(scope):
        return {"count": 0}
    await _beat(scope, str(current_user.user_id))
    return {"count": await _count(scope)}


@router.post("/live-presence/{scope}/leave")
async def leave(
    scope: str,
    current_user: User = Depends(get_current_student),
):
    """Remove the current student from `scope` (on leaving / submitting)."""
    if not _valid(scope):
        return {"count": 0}
    await _leave(scope, str(current_user.user_id))
    return {"count": await _count(scope)}


@router.get("/live-presence/{scope}")
async def get_count(
    scope: str,
    current_user: User = Depends(get_current_student),
):
    """Live count for a single scope."""
    if not _valid(scope):
        return {"count": 0}
    return {"count": await _count(scope)}


@router.get("/live-presence")
async def get_counts(
    exam_ids: Optional[str] = Query(
        None, description="Comma-separated scopes (exam id or '<exam>p<part>')"
    ),
    current_user: User = Depends(get_current_student),
):
    """Batch live counts for the cards a list page is showing."""
    if not exam_ids:
        return {"counts": {}}
    scopes: List[str] = []
    for raw in exam_ids.split(","):
        raw = raw.strip()
        if _valid(raw) and raw not in scopes:
            scopes.append(raw)
            if len(scopes) >= MAX_BATCH_SCOPES:
                break
    return {"counts": await _counts(scopes)}
