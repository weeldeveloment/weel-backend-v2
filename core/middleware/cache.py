import hashlib
import json
import random
import time
import uuid

from django.conf import settings
from django.core.cache import cache
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


def _build_cache_key(tenant_key: str, version: int, path: str, query_string: str) -> str:
    raw = f"{path}:{query_string}"
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
    try:
        cache.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, token)
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

        if request.path in EXEMPT_PATHS:
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
            token, version, request.path, request.META.get("QUERY_STRING", "")
        )

        try:
            cached = cache.get(cache_key)
        except Exception:
            cached = None

        if cached is not None:
            age = time.time() - cached.get("cached_at", 0)
            if age < DEFAULT_TTL:
                return HttpResponse(
                    json.dumps(cached["data"]),
                    status=cached["status"],
                    content_type=cached["content_type"],
                )
            if age < DEFAULT_TTL + GRACE_PERIOD:
                lock_key = _build_lock_key(cache_key)
                lock_token = _acquire_lock(lock_key)
                if lock_token:
                    try:
                        response = self.get_response(request)
                        if response.status_code == 200 and hasattr(response, "data"):
                            self._store_cache(cache_key, response)
                        return response
                    finally:
                        _release_lock(lock_key, lock_token)
                return HttpResponse(
                    json.dumps(cached["data"]),
                    status=cached["status"],
                    content_type=cached["content_type"],
                )

        lock_key = _build_lock_key(cache_key)
        lock_token = _acquire_lock(lock_key)

        if lock_token:
            try:
                response = self.get_response(request)
                if response.status_code == 200 and hasattr(response, "data"):
                    self._store_cache(cache_key, response)
                return response
            finally:
                _release_lock(lock_key, lock_token)

        cached = _wait_for_cache(cache_key, LOCK_WAIT_MAX)
        if cached is not None:
            return HttpResponse(
                json.dumps(cached["data"]),
                status=cached["status"],
                content_type=cached["content_type"],
            )

        response = self.get_response(request)
        if response.status_code == 200 and hasattr(response, "data"):
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
                cache.set(f"{CACHE_VERSION_PREFIX}:{token}", 1)
            except Exception:
                pass
