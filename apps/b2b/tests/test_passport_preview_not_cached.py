"""``core.middleware.cache.CacheMiddleware`` OCR natijasini so'rash
(polling) endpointini keshlamasligini tekshiradi.

Bu endpointning butun vazifasi — sekundlar ichida O'ZGARADIGAN holatni
qaytarish. Uni keshlash ish tejamaydi, aksincha klient kutayotgan
o'zgarishni yashiradi: birinchi so'rovga "pending" javobi keshlanib,
keyingi ~60 soniya davomida o'sha javob qaytarilardi va foydalanuvchi
OCR allaqachon tugagan bo'lsa ham kutib turardi (o'lchangan holat: OCR
8.8s da tugagan, javob 61s kechikkan)."""
from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory

from core.middleware.cache import CacheMiddleware


def _get(path, view):
    request = RequestFactory().get(path, HTTP_AUTHORIZATION="Bearer test-token")
    return CacheMiddleware(view)(request)


def _counting_view(responses):
    calls = {"n": 0}

    def view(_request):
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        response = JsonResponse(responses[index])
        response.data = responses[index]
        return response

    return view, calls


def test_passport_preview_status_is_not_served_from_cache():
    cache.clear()
    view, calls = _counting_view([{"status": "pending"}, {"status": "done", "full_name": "X"}])
    path = "/api/b2b/employees/passport-preview/abc123/"

    assert b"pending" in _get(path, view).content
    # Ikkinchi so'rov view'ga yetib borishi va YANGI holatni qaytarishi shart.
    assert b"done" in _get(path, view).content
    assert calls["n"] == 2


def test_other_endpoints_are_still_cached():
    cache.clear()
    view, calls = _counting_view([{"value": 1}, {"value": 2}])
    path = "/api/b2b/employees/"

    _get(path, view)
    _get(path, view)
    assert calls["n"] == 1
