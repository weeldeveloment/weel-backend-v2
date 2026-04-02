import asyncio
import json
import logging
import threading

from json import JSONDecodeError

from django.http import HttpResponse, HttpResponseForbidden
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from telegram import Update

from .setup import build_application, get_webhook_secret

logger = logging.getLogger(__name__)

_app = None
_loop = None
_lock = threading.Lock()


def _ensure_ready():
    global _app, _loop
    if _app is not None:
        return
    with _lock:
        if _app is not None:
            return
        _loop = asyncio.new_event_loop()
        thread = threading.Thread(target=_loop.run_forever, daemon=True)
        thread.start()

        _app = build_application()
        fut = asyncio.run_coroutine_threadsafe(_app.initialize(), _loop)
        fut.result(timeout=10)


@method_decorator(csrf_exempt, name="dispatch")
class TelegramWebhookView(View):
    def post(self, request, secret_token):
        if secret_token != get_webhook_secret():
            logger.warning("Invalid webhook secret token received.")
            return HttpResponseForbidden("Forbidden")

        try:
            data = json.loads(request.body)
        except JSONDecodeError as e:
            logger.warning("Invalid webhook payload (not JSON): %s", e)
            return HttpResponse("ok")
        try:
            _ensure_ready()
            if _app is None or _loop is None:
                logger.warning("Bot application not initialized yet.")
                return HttpResponse("ok")
            update = Update.de_json(data, _app.bot)
            fut = asyncio.run_coroutine_threadsafe(
                _app.process_update(update), _loop
            )
            fut.result(timeout=30)
        except Exception:
            logger.exception("Error processing Telegram webhook update")

        return HttpResponse("ok")
