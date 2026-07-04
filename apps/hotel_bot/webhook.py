import asyncio
import json
import logging
import threading
from concurrent.futures import Future
from json import JSONDecodeError

from django.http import HttpResponse, HttpResponseForbidden
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from telegram import Update

from .setup import build_application, get_webhook_secret

logger = logging.getLogger(__name__)

_loop = None
_loop_thread = None
_app = None
_init_future: Future | None = None
_lock = threading.Lock()
_INIT_TIMEOUT = 30.0


def _start_loop_once() -> None:
    global _loop, _loop_thread
    if _loop is not None:
        return
    with _lock:
        if _loop is not None:
            return
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(
            target=_loop.run_forever,
            daemon=True,
            name="hotel-bot-loop",
        )
        _loop_thread.start()


def _ensure_ready() -> None:
    global _app, _init_future
    _start_loop_once()

    with _lock:
        if (
            _init_future is not None
            and _init_future.done()
            and _init_future.exception() is None
        ):
            return

        if _init_future is not None and _init_future.done() and _init_future.exception() is not None:
            _init_future = None
            _app = None

        if _init_future is None:
            if _app is None:
                _app = build_application()
            _init_future = asyncio.run_coroutine_threadsafe(
                _app.initialize(), _loop
            )

    try:
        _init_future.result(timeout=_INIT_TIMEOUT)
    except Exception:
        logger.exception("Hotel bot application initialization failed")
        raise


@method_decorator(csrf_exempt, name="dispatch")
class HotelBotWebhookView(View):
    def post(self, request, secret_token):
        if secret_token != get_webhook_secret():
            logger.warning("Invalid hotel bot webhook secret token received.")
            return HttpResponseForbidden("Forbidden")

        try:
            data = json.loads(request.body)
        except JSONDecodeError as e:
            logger.warning("Invalid hotel bot webhook payload (not JSON): %s", e)
            return HttpResponse("ok")

        try:
            _ensure_ready()
            if _app is None or _loop is None:
                logger.warning("Hotel bot application not initialized.")
                return HttpResponse("ok")
            update = Update.de_json(data, _app.bot)
            fut = asyncio.run_coroutine_threadsafe(
                _app.process_update(update), _loop
            )
            fut.result(timeout=30)
        except Exception:
            logger.exception("Error processing hotel bot webhook update")

        return HttpResponse("ok")
