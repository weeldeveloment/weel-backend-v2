"""``core.middleware.cache.CacheMiddleware`` qulfni (lock) haqiqatan
bo'shatishini va 204 javobni ham keshlashini tekshiradi.

Ikkalasi birga bitta sekinlikni keltirib chiqargan edi.

``_release_lock`` ``cache.eval(...)`` ni chaqirardi — bunday metod na Django
kesh API'sida, na django-redis'ning ``RedisCache`` sinfida bor. Chaqiruv har
safar ``AttributeError`` bilan yiqilib, pastdagi ``except Exception: pass``
ichida yo'q bo'lardi, ya'ni **birorta ham qulf hech qachon bo'shatilmagan**.
Qulf o'zining to'liq ``LOCK_TIMEOUT`` (10 soniya) muddatini kutardi va shu
vaqt ichida o'sha kalitni so'ragan har bir so'rov ``_wait_for_cache`` ichida
butun ``LOCK_WAIT_MAX`` (0.5 s) uxlab, keyin ishni baribir o'zi bajarardi.

Ikkinchi yarmi: faqat 200 javob keshlanardi. Shuning uchun 204 qaytaradigan
endpoint kaliti hech qachon to'lmasdi — demak keyingi har bir so'rov keshni
topmay, qulfni ololmay, yarim soniya uxlardi. O'lchangan natija: mobil bosh
ekrandagi ``GET /employee-of-month/`` (bo'sh javob, 1 ms lik ish) har safar
550 ms olardi.
"""
import os
import time

import pytest
from django.core.cache import cache, caches
from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, override_settings

from core.middleware import cache as cache_middleware
from core.middleware.cache import CacheMiddleware


def _get(path, view):
    request = RequestFactory().get(path, HTTP_AUTHORIZATION="Bearer test-token")
    return CacheMiddleware(view)(request)


def _counting_view(response_factory):
    calls = {"n": 0}

    def view(_request):
        calls["n"] += 1
        return response_factory()

    return view, calls


def _no_content_view():
    response = HttpResponse(status=204)
    response.data = None
    return response


def test_release_lock_removes_the_lock_it_took():
    cache.clear()
    key = "weel:test:lock:mine"

    token = cache_middleware._acquire_lock(key)
    assert token is not None

    cache_middleware._release_lock(key, token)
    assert cache.get(key) is None


def test_release_lock_leaves_somebody_elses_lock_alone():
    """Muddati tugab, qulfni boshqa so'rov olib ulgurgan bo'lsa — tegmaydi."""
    cache.clear()
    key = "weel:test:lock:theirs"
    cache.add(key, "boshqa-so'rovniki", timeout=cache_middleware.LOCK_TIMEOUT)

    cache_middleware._release_lock(key, "meniki")

    assert cache.get(key) == "boshqa-so'rovniki"


def test_a_served_request_leaves_no_lock_behind():
    cache.clear()
    view, _ = _counting_view(lambda: _json({"value": 1}))
    path = "/api/b2b/workspace/team/"

    _get(path, view)

    left_over = [k for k in _lock_keys()]
    assert left_over == [], f"qulf qolib ketdi: {left_over}"


def test_204_is_cached_and_served_empty():
    cache.clear()
    view, calls = _counting_view(_no_content_view)
    path = "/api/b2b/workspace/employee-of-month/"

    first = _get(path, view)
    second = _get(path, view)

    assert first.status_code == 204
    assert second.status_code == 204
    # Keshdan qaytgan 204 tanasiz bo'lishi kerak — `json.dumps(None)` bergan
    # to'rt bayt `null` emas.
    assert second.content == b""
    assert calls["n"] == 1


def test_a_repeated_204_does_not_sit_out_the_lock_wait():
    """Aynan shu sekinlik uchun yozilgan test: ikkinchi so'rov kutmasligi shart."""
    cache.clear()
    view, _ = _counting_view(_no_content_view)
    path = "/api/b2b/workspace/employee-of-month/"

    _get(path, view)
    started = time.monotonic()
    _get(path, view)
    elapsed = time.monotonic() - started

    assert elapsed < cache_middleware.LOCK_WAIT_MAX / 2, f"{elapsed:.3f}s kutdi"


def _json(payload):
    response = JsonResponse(payload)
    response.data = payload
    return response


def _lock_keys():
    """Keshda qolgan qulf kalitlari — Redis bo'lmasa bo'sh ro'yxat."""
    try:
        client = cache.client.get_client(write=True)
    except Exception:  # pragma: no cover - Redis'siz muhit
        return []
    prefix = str(cache.client.make_key(cache_middleware.CACHE_LOCK_PREFIX))
    return [k.decode() if isinstance(k, bytes) else k for k in client.scan_iter(f"{prefix}*")]


# -- Redis ------------------------------------------------------------------
#
# Yuqoridagi testlar `core.test_settings` bergan LocMemCache'da ishlaydi va
# `_release_lock` ning zaxira (fallback) yo'lini tekshiradi. Asl xato esa
# aynan Redis yo'lida edi va u yerda ikkinchi tuzoq ham bor: django-redis har
# bir kalitga prefiks qo'shadi (`:1:weel:cache:lock:v1:...`) va har bir
# qiymatni pickle qiladi. Lua skriptga xom kalit va xom token berilsa —
# hech nimani topmaydi va hech nimani o'chirmaydi, ya'ni tuzatishdan oldingi
# holat qaytadi. Shuning uchun bu testlar haqiqiy Redis'ga tegadi.

REDIS_URL = (os.environ.get("REDIS_CONNECTION_STRING") or "redis://127.0.0.1:6381/1").strip()

redis_cache = override_settings(
    CACHES={
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        }
    }
)


@pytest.fixture
def redis_backed():
    """Haqiqiy Redis'ga ulangan kesh; Redis bo'lmasa test o'tkazib yuboriladi."""
    with redis_cache:
        caches.close_all()
        try:
            caches["default"].set("weel:test:ping", "1", timeout=5)
        except Exception as exc:
            pytest.skip(f"Redis yo'q ({REDIS_URL}): {exc}")
        caches["default"].delete("weel:test:ping")
        yield caches["default"]
        caches.close_all()


def test_release_lock_works_against_real_redis(redis_backed):
    key = "weel:test:lock:redis"
    redis_backed.delete(key)

    token = cache_middleware._acquire_lock(key)
    assert token is not None
    # Qulf haqiqatan Redis'da, prefiksli kalit ostida yotibdi.
    assert redis_backed.get(key) == token

    cache_middleware._release_lock(key, token)
    assert redis_backed.get(key) is None


def test_release_lock_against_real_redis_spares_another_holder(redis_backed):
    key = "weel:test:lock:redis-theirs"
    redis_backed.delete(key)
    redis_backed.add(key, "boshqa-so'rovniki", timeout=cache_middleware.LOCK_TIMEOUT)

    cache_middleware._release_lock(key, "meniki")

    assert redis_backed.get(key) == "boshqa-so'rovniki"
    redis_backed.delete(key)
