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

Displayed count = real takers + a synthetic "social proof" number, as in VN (ported
on the owner's request, 2026-10). Set LIVE_PRESENCE_SYNTHETIC=0 to show real takers
only.
"""
import hashlib
import os
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

# ── Synthetic activity (VN) ─────────────────────────────────────────────────
# A per-scope number that does a smooth random walk: every FAKE_TICK seconds it
# moves -1 (25%) / holds (50%) / +1 (25%) and is clamped to its tier's range. The
# step is a hash of (scope, tick), so every worker evolves a scope identically.
# State ("value:tick") lives in Redis, or in this process when Redis is down.
FAKE_ENABLED = os.getenv("LIVE_PRESENCE_SYNTHETIC", "1").strip().lower() not in ("0", "false", "off", "no")
FAKE_TICK = 30            # seconds per random-walk step
FAKE_MAX_CATCHUP = 240    # cap on steps applied when a scope was not read for a while
# The first FEATURED_COUNT exams of each skill get the higher band, the rest the lower.
TIER_A_RANGE = (10, 100)
TIER_B_RANGE = (5, 50)
FEATURED_COUNT = 6
FEATURED_TTL = 600        # re-query the "first 6 per skill" set at most every 10 min

_featured = {"ids": set(), "ts": 0.0}
_fake_memory: Dict[str, str] = {}   # scope -> "value:tick" (fallback state)


def _featured_ids() -> set:
    """exam ids among the first FEATURED_COUNT of their skill, by ascending id.
    Cached; keeps the last known set if the query fails."""
    now = time.time()
    if _featured["ts"] and (now - _featured["ts"] < FEATURED_TTL):
        return _featured["ids"]
    try:
        from app.database import SessionLocal
        from app.models.models import ExamSection, WritingTask
        ids = set()
        db = SessionLocal()
        try:
            for stype in ("reading", "listening"):
                rows = (db.query(ExamSection.exam_id)
                        .filter(ExamSection.section_type == stype)
                        .distinct().order_by(ExamSection.exam_id.asc())
                        .limit(FEATURED_COUNT).all())
                ids.update(r[0] for r in rows if r[0] is not None)
            wrows = (db.query(WritingTask.test_id).distinct()
                     .order_by(WritingTask.test_id.asc())
                     .limit(FEATURED_COUNT).all())
            ids.update(r[0] for r in wrows if r[0] is not None)
        finally:
            db.close()
        _featured["ids"] = ids
    except Exception:
        pass
    _featured["ts"] = now   # also after a failure, so a DB outage is not retried per call
    return _featured["ids"]


def _tier_bounds(scope: str, featured: set):
    try:
        eid = int(scope.split("p", 1)[0])
    except ValueError:
        return TIER_B_RANGE
    return TIER_A_RANGE if eid in featured else TIER_B_RANGE


def _hash_int(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


def _walk_step(scope: str, tick: int) -> int:
    """Deterministic per (scope, tick) step: -1 (25%) / 0 (50%) / +1 (25%)."""
    r = (_hash_int(f"walk:{scope}:{tick}") % 1000) / 1000.0
    if r < 0.25:
        return -1
    if r < 0.75:
        return 0
    return 1


def _advance(scope: str, raw: Optional[str], cur_tick: int, lo: int, hi: int):
    """(value, new_state_or_None) from the stored "value:tick" state."""
    try:
        val_s, tick_s = (raw or "").split(":", 1)
        value = max(lo, min(hi, int(val_s)))
        stored_tick = int(tick_s)
    except ValueError:
        # First read of this scope: deterministic seed inside the tier range.
        value = lo + (_hash_int(f"seed:{scope}") % (hi - lo + 1))
        return value, f"{value}:{cur_tick}"
    if cur_tick <= stored_tick:
        return value, None
    steps = min(cur_tick - stored_tick, FAKE_MAX_CATCHUP)
    for t in range(cur_tick - steps + 1, cur_tick + 1):
        value = max(lo, min(hi, value + _walk_step(scope, t)))
    return value, f"{value}:{cur_tick}"


async def _fake_counts(scopes: List[str]) -> Dict[str, int]:
    if not FAKE_ENABLED or not scopes:
        return {}
    featured = _featured_ids()
    cur_tick = int(time.time() // FAKE_TICK)
    bounds = {s: _tier_bounds(s, featured) for s in scopes}
    ttl = FAKE_TICK * (FAKE_MAX_CATCHUP + 20)
    client = cache.redis_client
    if client:
        try:
            raws = await client.mget([f"fake_walk:{s}" for s in scopes])
            out = {}
            pipe = client.pipeline()
            for s, raw in zip(scopes, raws):
                if isinstance(raw, bytes):
                    raw = raw.decode()
                value, state = _advance(s, raw, cur_tick, *bounds[s])
                out[s] = value
                if state:
                    pipe.set(f"fake_walk:{s}", state, ex=ttl)
                else:
                    # keep an actively viewed scope's walk state alive
                    pipe.expire(f"fake_walk:{s}", ttl)
            await pipe.execute()
            return out
        except Exception:
            pass
    out = {}
    if len(_fake_memory) > MAX_MEMORY_SCOPES:
        _fake_memory.clear()
    for s in scopes:
        value, state = _advance(s, _fake_memory.get(s), cur_tick, *bounds[s])
        out[s] = value
        if state:
            _fake_memory[s] = state
    return out


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
    """Displayed count per scope = real live takers + synthetic activity."""
    if not scopes:
        return {}
    real = await _real_counts(scopes)
    fake = await _fake_counts(scopes)
    return {scope: real.get(scope, 0) + fake.get(scope, 0) for scope in scopes}


async def _real_counts(scopes: List[str]) -> Dict[str, int]:
    """Real takers per scope. One Redis round trip for the whole batch."""
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
