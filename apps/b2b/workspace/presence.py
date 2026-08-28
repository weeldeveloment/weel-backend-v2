"""Who is online right now.

Not the same question as ``b2b_employee.status`` — that column says
``available``/``on_trip``/``blocked``, which is where somebody *is*, decided by
a manager and true for days at a time. This is whether a phone is holding a
socket open this minute, and it is deliberately kept out of the database: it
changes on every lift and tunnel, and a write per change would be a write per
commute for every employee in the company.

It lives in the shared cache instead, which in production is the same Redis the
channel layer already runs on, so every worker sees the same answer. In DEBUG
with no Redis the cache is per-process — that is fine, because so is the
in-memory channel layer, and a single dev worker is consistent with itself.

Two keys per employee:

* ``conn``  — how many sockets they currently have open. A counter rather than
  a flag because a phone and a tablet are two connections, and closing one must
  not put the person offline on both.
* ``seen``  — when they were last here, kept long after they go offline so the
  chat header can say "oxirgi ko'rilgan …" rather than nothing at all.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from django.core.cache import cache

logger = logging.getLogger(__name__)

#: How long a connection counter survives without a heartbeat. Longer than the
#: client's ping interval by a wide margin: a phone on a bad connection that is
#: a little late with its ping should not flicker offline and back.
CONNECTION_TTL_SECONDS = 180

#: Heartbeat the client is expected to send. Exported so the consumer and the
#: app can be read against the same number.
HEARTBEAT_SECONDS = 45

#: "Last seen" outlives the session by a fortnight. Past that the header says
#: nothing rather than a date nobody needs.
SEEN_TTL_SECONDS = 60 * 60 * 24 * 14


def _conn_key(employee_id: int) -> str:
    return f"ws:presence:conn:{employee_id}"


def _seen_key(employee_id: int) -> str:
    return f"ws:presence:seen:{employee_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_online(employee_id: int) -> bool:
    """Registers one open socket. True when this is the one that put them
    online — the caller broadcasts only on that, so a second device does not
    announce an arrival that already happened."""
    key = _conn_key(employee_id)
    try:
        # `add` only writes when the key is absent, so two sockets connecting
        # at once cannot both reset the counter to zero and lose one another.
        cache.add(key, 0, CONNECTION_TTL_SECONDS)
        count = cache.incr(key)
    except ValueError:
        # The key expired between the `add` and the `incr`. Treat it as the
        # first connection, which is what it now is.
        cache.set(key, 1, CONNECTION_TTL_SECONDS)
        count = 1
    except Exception:  # noqa: BLE001
        logger.exception("Could not mark employee %s online", employee_id)
        return False

    touch(employee_id)
    return count == 1


def touch(employee_id: int) -> None:
    """Heartbeat: keeps the counter alive and moves "last seen" forward."""
    try:
        cache.touch(_conn_key(employee_id), CONNECTION_TTL_SECONDS)
        cache.set(_seen_key(employee_id), _now_iso(), SEEN_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.exception("Could not refresh presence for employee %s", employee_id)


def mark_offline(employee_id: int) -> bool:
    """Drops one open socket. True when the last one went — the caller
    broadcasts only on that."""
    key = _conn_key(employee_id)
    try:
        count = cache.decr(key)
    except ValueError:
        # Already gone (expired, or never registered). Nothing to announce.
        count = 0
    except Exception:  # noqa: BLE001
        logger.exception("Could not mark employee %s offline", employee_id)
        return False

    if count <= 0:
        cache.delete(key)
    # Written on the way out too: the moment they disappeared *is* the last
    # time they were seen, and without this the header would show the time of
    # their final heartbeat, up to a minute early.
    try:
        cache.set(_seen_key(employee_id), _now_iso(), SEEN_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.exception("Could not record last-seen for employee %s", employee_id)
    return count <= 0


def online_ids(employee_ids: Iterable[int]) -> set[int]:
    """Which of these employees have a socket open.

    Asked against a roster the caller already holds — a company's is tens of
    rows — so there is no company-wide set to keep in step with reality.
    """
    ids = [int(i) for i in employee_ids]
    if not ids:
        return set()
    try:
        found = cache.get_many([_conn_key(i) for i in ids])
    except Exception:  # noqa: BLE001
        logger.exception("Could not read presence")
        return set()
    return {i for i in ids if (found.get(_conn_key(i)) or 0) > 0}


def last_seen(employee_ids: Iterable[int]) -> dict[int, str]:
    """When each of these was last here, as ISO strings. Missing means never
    — or longer ago than [SEEN_TTL_SECONDS]."""
    ids = [int(i) for i in employee_ids]
    if not ids:
        return {}
    try:
        found = cache.get_many([_seen_key(i) for i in ids])
    except Exception:  # noqa: BLE001
        logger.exception("Could not read last-seen")
        return {}
    return {i: found[_seen_key(i)] for i in ids if found.get(_seen_key(i))}
