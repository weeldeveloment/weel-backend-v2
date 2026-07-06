from __future__ import annotations

from datetime import datetime

from django.core.cache import cache

# Time given to a client to complete payment before the soft hold on a slot expires
# and the resource becomes bookable by someone else again. The real correctness
# guarantee is the `no_overlapping_resource_booking` EXCLUDE constraint in the DB —
# this is purely to make the checkout UX not race two clients against each other.
HOLD_TTL_SECONDS = 5 * 60

_HOLD_KEY_PREFIX = "activity:hold"


def _hold_key(resource_id: int, starts_at: datetime, blocked_until: datetime) -> str:
    return f"{_HOLD_KEY_PREFIX}:{resource_id}:{starts_at.isoformat()}:{blocked_until.isoformat()}"


def _index_key(resource_id: int) -> str:
    return f"{_HOLD_KEY_PREFIX}:index:{resource_id}"


def acquire_hold(*, resource_id: int, starts_at: datetime, blocked_until: datetime, booking_guid: str) -> bool:
    """Atomic SETNX — only one request can hold a given (resource, interval)."""
    key = _hold_key(resource_id, starts_at, blocked_until)
    acquired = cache.add(key, booking_guid, timeout=HOLD_TTL_SECONDS)
    if acquired:
        _remember_interval(resource_id, starts_at, blocked_until)
    return acquired


def release_hold(*, resource_id: int, starts_at: datetime, blocked_until: datetime) -> None:
    cache.delete(_hold_key(resource_id, starts_at, blocked_until))


def _remember_interval(resource_id: int, starts_at: datetime, blocked_until: datetime) -> None:
    """
    Cache backends have no "list keys by prefix" primitive that works uniformly
    across LocMem/Redis, so we keep a small side-index per resource (also TTL'd)
    listing the intervals currently held, for fetch_held_intervals_for_resources
    to scan without needing SCAN/KEYS in production Redis.
    """
    index_key = _index_key(resource_id)
    intervals = cache.get(index_key) or []
    intervals.append((starts_at.isoformat(), blocked_until.isoformat()))
    cache.set(index_key, intervals, timeout=HOLD_TTL_SECONDS)


def fetch_held_intervals_for_resources(
    *, resource_ids: list[int], from_dt: datetime, to_dt: datetime,
) -> dict[int, list[tuple[datetime, datetime]]]:
    result: dict[int, list[tuple[datetime, datetime]]] = {rid: [] for rid in resource_ids}
    for rid in resource_ids:
        index_key = _index_key(rid)
        intervals = cache.get(index_key) or []
        for start_iso, end_iso in intervals:
            start = datetime.fromisoformat(start_iso)
            end = datetime.fromisoformat(end_iso)
            # Skip entries whose hold key has already expired/been released —
            # the index list itself only expires as a whole, so individual
            # entries can go stale between writes.
            if cache.get(_hold_key(rid, start, end)) is None:
                continue
            if end <= from_dt or start >= to_dt:
                continue
            result[rid].append((start, end))
    return result
