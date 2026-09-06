import hashlib
import json
import random
import time
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse

CACHE_PREFIX = "weel:cache:v1"
CACHE_VERSION_PREFIX = "weel:cache:v1:version"
CACHE_LOCK_PREFIX = "weel:cache:lock:v1"
DEFAULT_TTL = getattr(settings, "CACHE_TTL_SECONDS", 60)
GRACE_PERIOD = int(DEFAULT_TTL * 0.5)
LOCK_TIMEOUT = 10
LOCK_WAIT_MAX = 0.5
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

EXEMPT_PATHS = {
    "/api/b2b/auth/login/",
    "/api/b2b/auth/login/verify/",
    "/api/admin/auth/login/",
    "/api/admin/auth/login/verify/",
}

# Endpoints whose whole purpose is to report state that changes *between*
# two requests seconds apart. Caching a GET here does not save work, it
# hides the very change the client is polling for: the passport OCR job
# status returned "pending" on the first poll, that response was served
# from cache for the next ~60s, and the client kept waiting long after the
# job had finished (measured: OCR done in 8.8s, answer delivered 61s later).
# Path prefixes, because the job id is part of the URL.
#
# The B2B mobile workspace has the same problem for a different reason: the
# version key that invalidates a cached GET is keyed by *access token*, so a
# write only clears the cache of the person who made it. A manager creating a
# task, or a colleague sending a chat message, leaves every other phone in the
# company serving a stale list for up to a minute — which on a device that
# refreshes by pull-down reads as a frozen app. The roster stays cached:
# it is an expensive query and it barely changes.
EXEMPT_PATH_PREFIXES = (
    "/api/b2b/employees/passport-preview/",
    "/api/b2b/workspace/me/",
    "/api/b2b/workspace/tasks/",
    "/api/b2b/workspace/events/",
    "/api/b2b/workspace/chats/",
    # A lead board is first-come-first-served: two people looking at it must
    # see the same thing, or one of them spends a minute working a lead that
    # was claimed before they opened it. Files are the same argument more
    # mildly — a colleague uploads a waybill and nobody else can see it yet.
    "/api/b2b/workspace/leads/",
    "/api/b2b/workspace/files/",
    # Mail must never be cached. Opening a thread is what marks it read, and a
    # cache hit would skip that write, so the message would keep reappearing as
    # unread. The inbox list has the same freshness argument as chat.
    "/api/b2b/workspace/mail/",
    "/api/b2b/workspace/notifications/",
)

AUTH_HEADER_PREFIX = "Bearer "
RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _get_auth_token(request) -> str:
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if auth.startswith(AUTH_HEADER_PREFIX):
        return auth[len(AUTH_HEADER_PREFIX):]
    return auth


def _build_cache_key(
    tenant_key: str, version: int, path: str, query_string: str, language: str = ""
) -> str:
    # Labels the server composes (roles, modules, permissions) follow the
    # request language, so a Russian and an Uzbek reader must not share an
    # entry — `language` is what the locale middleware resolved just before.
    raw = f"{path}:{query_string}:{language}"
    path_hash = hashlib.md5(raw.encode()).hexdigest()
    return f"{CACHE_PREFIX}:{tenant_key}:{version}:{path_hash}"


def _build_lock_key(cache_key: str) -> str:
    return f"{CACHE_LOCK_PREFIX}:{cache_key}"


def _acquire_lock(lock_key: str) -> str | None:
    token = str(uuid.uuid4())
    try:
        acquired = cache.add(lock_key, token, timeout=LOCK_TIMEOUT)
    except Exception:
        return None
    return token if acquired else None


def _release_lock(lock_key: str, token: str) -> None:
    """Drops the lock this request took, and only that one.

    `cache.eval` does not exist. Django's cache API has no such method and
    neither does django-redis's `RedisCache` — the call raised AttributeError
    on every single release, straight into an `except Exception: pass`, so
    **no lock was ever released**. Each one instead sat out its full
    `LOCK_TIMEOUT` of ten seconds, during which every other request for the
    same key fell through to `_wait_for_cache` and slept the whole
    `LOCK_WAIT_MAX` before doing the work anyway. Measured on the mobile home
    screen: `GET /employee-of-month/`, which answers 204 in well under a
    millisecond, took a flat 550 ms on every call.

    The script is still the right thing to run — the check and the delete have
    to be one round trip, or a lock that expired and was retaken by somebody
    else is deleted by its previous holder. It has to be run against what
    Redis actually holds, though, which is not what `_acquire_lock` was
    handed: django-redis prefixes every key (`:1:weel:cache:lock:v1:...`) and
    pickles every value, so a script given the bare key and the bare token
    matches nothing and deletes nothing. `make_key` and `encode` are the same
    two transformations the write went through.
    """
    try:
        client = cache.client.get_client(write=True)
        redis_key = str(cache.client.make_key(lock_key))
        redis_value = cache.client.encode(token)
    except Exception:
        client = None

    if client is not None:
        try:
            client.eval(RELEASE_LOCK_SCRIPT, 1, redis_key, redis_value)
            return
        except Exception:
            pass

    # A cache backend that is not Redis. The read and the delete are two calls
    # here, so a lock that expires between them can be taken by somebody else
    # and then deleted by us — which costs that request a duplicated query and
    # nothing else. Not worth a second lock implementation to close.
    try:
        if cache.get(lock_key) == token:
            cache.delete(lock_key)
    except Exception:
        pass


def _wait_for_cache(cache_key: str, max_wait: float) -> dict | None:
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            return cached
        time.sleep(0.05)
    return None


CACHEABLE_STATUSES = (200, 204)


def _is_cacheable(response) -> bool:
    """Whether this answer is worth remembering.

    204 belongs here as much as 200 does, and leaving it out was not a
    conservative choice — it was the thing that made every miss cost the full
    `LOCK_WAIT_MAX`. "Nobody has been named employee of the month" is a real,
    stable answer that the app asks for on every home screen; unstored, the
    key it would live under stayed empty forever, so every request found no
    cache, failed to take the lock, and slept.
    """
    return response.status_code in CACHEABLE_STATUSES and hasattr(response, "data")


def _response_from_cache(cached: dict) -> HttpResponse:
    """Rebuilds a stored answer.

    A 204 is rebuilt empty. `json.dumps(None)` is the four bytes `null`, and a
    204 carrying a body is a response no client is required to read — the app's
    own `ApiClient` treats an empty 204 as "nothing here" and would have to be
    taught otherwise.
    """
    status_code = cached["status"]
    if status_code == 204 or cached.get("data") is None:
        return HttpResponse(status=status_code)
    return HttpResponse(
        json.dumps(cached["data"], cls=DjangoJSONEncoder),
        status=status_code,
        content_type=cached["content_type"],
    )


class CacheMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in SAFE_METHODS:
            response = self.get_response(request)
            if response.status_code < 400:
                self._invalidate(request)
            return response

        token = _get_auth_token(request)
        if not token:
            return self.get_response(request)

        if request.path in EXEMPT_PATHS or request.path.startswith(EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        cache_control = request.META.get("HTTP_CACHE_CONTROL", "")
        if "no-cache" in cache_control or "no-store" in cache_control:
            return self.get_response(request)

        if request.GET.get("nocache"):
            return self.get_response(request)

        try:
            version = cache.get(f"{CACHE_VERSION_PREFIX}:{token}", 1)
        except Exception:
            return self.get_response(request)

        cache_key = _build_cache_key(
            token,
            version,
            request.path,
            request.META.get("QUERY_STRING", ""),
            getattr(request, "LANGUAGE_CODE", "") or "",
        )

        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None

        if cached is not None:
            age = time.time() - cached.get("cached_at", 0)
            if age < DEFAULT_TTL:
                return _response_from_cache(cached)
            if age < DEFAULT_TTL + GRACE_PERIOD:
                lock_key = _build_lock_key(cache_key)
                lock_token = _acquire_lock(lock_key)
                if lock_token:
                    try:
                        response = self.get_response(request)
                        if _is_cacheable(response):
                            self._store_cache(cache_key, response)
                        return response
                    finally:
                        _release_lock(lock_key, lock_token)
                return _response_from_cache(cached)

        lock_key = _build_lock_key(cache_key)
        lock_token = _acquire_lock(lock_key)

        if lock_token:
            try:
                response = self.get_response(request)
                if _is_cacheable(response):
                    self._store_cache(cache_key, response)
                return response
            finally:
                _release_lock(lock_key, lock_token)

        cached = _wait_for_cache(cache_key, LOCK_WAIT_MAX)
        if cached is not None:
            return _response_from_cache(cached)

        response = self.get_response(request)
        if _is_cacheable(response):
            self._store_cache(cache_key, response)
        return response

    def _store_cache(self, cache_key: str, response) -> None:
        jitter = random.randint(0, int(DEFAULT_TTL * 0.3))
        try:
            cache.set(
                cache_key,
                {
                    "data": response.data,
                    "status": response.status_code,
                    "content_type": response.get("Content-Type", "application/json"),
                    "cached_at": time.time(),
                },
                timeout=DEFAULT_TTL + jitter,
            )
        except Exception:
            pass

    def _invalidate(self, request):
        token = _get_auth_token(request)
        if token:
            try:
                cache.incr(f"{CACHE_VERSION_PREFIX}:{token}")
            except ValueError:
                # No version key yet means every prior GET for this token was
                # cached under the implicit default (1, per the `cache.get(...,
                # 1)` fallback below) — setting it to 1 here would be a no-op
                # that leaves that exact response in place, so the first
                # mutation for a token would silently fail to invalidate
                # anything. Start at 2 instead, guaranteed to differ from the
                # default and so guaranteed to compute a fresh cache key.
                cache.set(f"{CACHE_VERSION_PREFIX}:{token}", 2)
            except Exception:
                pass
