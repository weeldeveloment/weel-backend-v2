"""``core.middleware.cache.CacheMiddleware`` jonli qo'ng'iroq holatini
keshlamasligini tekshiradi.

Chaqiruvchining telefoni ``GET /calls/<id>/`` ni har 4 soniyada so'raydi va
undan bitta narsani biladi: narigi tomon ko'tardimi. Bu javob keshlanganda
birinchi "ringing" javobi keyingi 60 soniya davomida qaytarilardi —
``_invalidate`` YOZGAN tokenning versiyasini oshiradi, ko'targan odam esa
kutayotgan odam emas. 60 soniya ayni paytda ring muddati ham, ya'ni
chaqiruvchi bu yo'l bilan javobni umuman bila olmasdi: uning ekrani
"Chaqirilmoqda…" da qolar, ko'targan odam esa xonada yolg'iz o'tirardi.
"""
from django.core.cache import cache
from django.http import JsonResponse
from django.test import RequestFactory

from core.middleware.cache import CacheMiddleware


def _get(path, view, token="caller-token"):
    request = RequestFactory().get(path, HTTP_AUTHORIZATION=f"Bearer {token}")
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


def test_call_state_is_never_served_from_cache():
    cache.clear()
    view, calls = _counting_view([{"status": "ringing"}, {"status": "accepted"}])
    path = "/api/b2b/workspace/calls/44/"

    assert b"ringing" in _get(path, view).content
    # Ikkinchi poll view'ga yetib borishi shart — aks holda chaqiruvchi
    # "qabul qilindi" ni hech qachon eshitmaydi.
    assert b"accepted" in _get(path, view).content
    assert calls["n"] == 2


def test_the_ring_this_phone_is_missing_is_not_cached_either():
    """``/calls/incoming/`` — ilova oldinga qaytganda o'tkazib yuborilgan
    qo'ng'iroqni shundan biladi. Bo'sh javobi keshlansa, telefon o'zini
    chaqirayotgan qo'ng'iroqni bir daqiqa ko'rmay turadi."""
    cache.clear()
    view, calls = _counting_view([{}, {"id": 44, "status": "ringing"}])
    path = "/api/b2b/workspace/calls/incoming/"

    _get(path, view)
    assert b"44" in _get(path, view).content
    assert calls["n"] == 2


def test_a_conference_is_not_cached_either():
    cache.clear()
    view, calls = _counting_view([{"active": False}, {"active": True}])
    path = "/api/b2b/workspace/conferences/7/"

    _get(path, view)
    assert b"true" in _get(path, view).content
    assert calls["n"] == 2
