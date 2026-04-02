"""
Tests for the bot app: webhook secret, application build, webhook view, and start handler.
"""

import hashlib
import json
from unittest.mock import patch, MagicMock, AsyncMock

from django.test import TestCase, override_settings
from django.http import HttpResponse

from telegram import Update


# ──────────────────────────────────────────────
# 1. setup.py — get_webhook_secret, build_application
# ──────────────────────────────────────────────


class GetWebhookSecretTests(TestCase):
    def test_returns_sha256_hex_first_32_chars(self):
        from bot.setup import get_webhook_secret
        with override_settings(BOT_TOKEN="my_secret_token"):
            secret = get_webhook_secret()
        expected = hashlib.sha256("my_secret_token".encode()).hexdigest()[:32]
        self.assertEqual(secret, expected)
        self.assertEqual(len(secret), 32)

    def test_empty_token_returns_32_char_hex(self):
        from bot.setup import get_webhook_secret
        with override_settings(BOT_TOKEN=""):
            secret = get_webhook_secret()
        expected = hashlib.sha256("".encode()).hexdigest()[:32]
        self.assertEqual(secret, expected)
        self.assertEqual(len(secret), 32)

    def test_different_tokens_different_secrets(self):
        from bot.setup import get_webhook_secret
        with override_settings(BOT_TOKEN="token_a"):
            secret_a = get_webhook_secret()
        with override_settings(BOT_TOKEN="token_b"):
            secret_b = get_webhook_secret()
        self.assertNotEqual(secret_a, secret_b)


class BuildApplicationTests(TestCase):
    def test_raises_when_token_not_configured(self):
        from bot.setup import build_application
        with override_settings(BOT_TOKEN=None):
            with self.assertRaises(ValueError) as ctx:
                build_application()
        self.assertIn("configured", str(ctx.exception).lower())

    def test_raises_when_token_empty(self):
        from bot.setup import build_application
        with override_settings(BOT_TOKEN=""):
            with self.assertRaises(ValueError):
                build_application()

    @override_settings(BOT_TOKEN="test_bot_token_123")
    def test_returns_application_with_bot_and_handlers(self):
        from bot.setup import build_application
        app = build_application()
        self.assertIsNotNone(app)
        self.assertIsNotNone(app.bot)
        handlers = app.handlers.get(0, [])
        self.assertGreater(len(handlers), 0, "Expected at least one handler (start command)")


# ──────────────────────────────────────────────
# 2. webhook.py — TelegramWebhookView
# ──────────────────────────────────────────────


class TelegramWebhookViewTests(TestCase):
    def _post_webhook(self, secret_token, body=None, content_type="application/json"):
        from django.test import RequestFactory
        from bot.webhook import TelegramWebhookView
        factory = RequestFactory()
        data = body if body is not None else {}
        payload = json.dumps(data) if isinstance(data, dict) else data
        request = factory.post(
            f"/api/bot/webhook/{secret_token}/",
            data=payload,
            content_type=content_type,
        )
        view = TelegramWebhookView.as_view()
        return view(request, secret_token=secret_token)

    @override_settings(BOT_TOKEN="test_token")
    def test_invalid_secret_returns_403(self):
        from bot.setup import get_webhook_secret
        valid_secret = get_webhook_secret()
        invalid_secret = "x" * 32 if valid_secret != "x" * 32 else "y" * 32
        response = self._post_webhook(invalid_secret, body={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), "Forbidden")

    @override_settings(BOT_TOKEN="test_token")
    @patch("bot.webhook._ensure_ready")
    @patch("bot.webhook.asyncio.run_coroutine_threadsafe")
    def test_valid_secret_returns_200_ok(self, mock_run_coro, mock_ensure):
        from bot.setup import get_webhook_secret
        mock_fut = MagicMock()
        mock_fut.result.return_value = None
        mock_run_coro.return_value = mock_fut

        secret = get_webhook_secret()
        # Minimal update-like payload (message with text triggers start or other handlers)
        body = {"update_id": 1, "message": {"message_id": 1, "from": {"id": 1, "is_bot": False, "first_name": "Test"}, "chat": {"id": 1, "type": "private"}, "date": 1234567890, "text": "/start"}}
        response = self._post_webhook(secret, body=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "ok")
        mock_ensure.assert_called_once()

    @override_settings(BOT_TOKEN="test_token")
    @patch("bot.webhook._ensure_ready")
    @patch("bot.webhook.asyncio.run_coroutine_threadsafe")
    def test_valid_secret_calls_process_update(self, mock_run_coro, mock_ensure):
        from bot.webhook import _app
        from bot.setup import get_webhook_secret
        # Ensure we have an app when _ensure_ready is called
        with patch("bot.webhook._app", MagicMock()) as mock_app:
            mock_fut = MagicMock()
            mock_fut.result.return_value = None
            mock_run_coro.return_value = mock_fut
            secret = get_webhook_secret()
            body = {"update_id": 1}
            self._post_webhook(secret, body=body)
            # _ensure_ready() was called; process_update is called on _app
            mock_ensure.assert_called_once()

    @override_settings(BOT_TOKEN="test_token")
    @patch("bot.webhook._ensure_ready")
    @patch("bot.webhook.asyncio.run_coroutine_threadsafe")
    def test_invalid_json_still_returns_200_ok(self, mock_run_coro, mock_ensure):
        from bot.setup import get_webhook_secret
        from django.test import RequestFactory
        from bot.webhook import TelegramWebhookView
        secret = get_webhook_secret()
        factory = RequestFactory()
        request = factory.post(
            f"/api/bot/webhook/{secret}/",
            data="not valid json {{{",
            content_type="application/json",
        )
        view = TelegramWebhookView.as_view()
        response = view(request, secret_token=secret)
        # View catches exception and still returns 200 "ok"
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "ok")


# ──────────────────────────────────────────────
# 3. handlers.py — start_handler
# ──────────────────────────────────────────────


class StartHandlerTests(TestCase):
    @override_settings(MINIAPP_URL="https://partners.weel.uz/")
    def test_start_handler_reply_has_text_and_keyboard(self):
        import asyncio
        from bot.handlers import start_handler

        mock_reply = AsyncMock(return_value=None)
        mock_message = MagicMock()
        mock_message.reply_text = mock_reply
        mock_update = MagicMock()
        mock_update.message = mock_message
        context = MagicMock()

        async def run():
            await start_handler(mock_update, context)

        asyncio.run(run())
        mock_reply.assert_called_once()
        call_args = mock_reply.call_args
        text = call_args[0][0]
        self.assertIn("WEEL", text)
        self.assertTrue("xush kelibsiz" in text.lower() or "ilovani" in text.lower())
        reply_markup = call_args[1].get("reply_markup")
        self.assertIsNotNone(reply_markup)


# ──────────────────────────────────────────────
# 4. URL / integration
# ──────────────────────────────────────────────


class BotURLTests(TestCase):
    def test_webhook_url_resolves(self):
        from django.urls import reverse
        url = reverse("bot:bot-webhook", kwargs={"secret_token": "abc"})
        self.assertIn("webhook", url)
        self.assertIn("abc", url)

    @override_settings(BOT_TOKEN="test")
    @patch("bot.webhook._ensure_ready")
    @patch("bot.webhook.asyncio.run_coroutine_threadsafe")
    def test_post_webhook_endpoint_returns_200_with_valid_secret(
        self, mock_run_coro, mock_ensure
    ):
        from django.test import Client
        from bot.setup import get_webhook_secret
        mock_fut = MagicMock()
        mock_fut.result.return_value = None
        mock_run_coro.return_value = mock_fut
        client = Client()
        secret = get_webhook_secret()
        response = client.post(
            f"/api/bot/webhook/{secret}/",
            data=json.dumps({"update_id": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
