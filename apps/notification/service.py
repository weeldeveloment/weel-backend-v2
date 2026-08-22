import logging

from django.conf import settings
from firebase_admin import messaging

from shared.raw.db import execute, fetch_all
from .raw_repository import create_notification


logger = logging.getLogger(__name__)


class B2BFirebaseNotConfigured(RuntimeError):
    """Raised when a B2B push is attempted with no B2B Firebase project wired up."""


def b2b_firebase_app():
    """The Firebase app a push to a B2B workspace employee must be sent from.

    The workspace app is registered in its own Firebase project, and an FCM
    token can only be addressed by the project that issued it — sending a B2B
    token from the consumer project fails with `SenderId mismatch`.

    Raises rather than falling back to the default app. The fallback used to be
    the safe-looking option, but there is no deployment where it does anything
    useful: every token this app addresses was issued by the B2B project, so
    the default app cannot deliver a single one of them. What it did instead
    was turn a missing credential into a per-token `SenderId mismatch` buried
    in the send loop, which reads like a token problem and hides the one thing
    that is actually wrong — see the deployment note below.

    The three B2B senders all call this inside a `try` that logs and carries
    on, so a misconfigured deployment loses its pushes exactly as before; the
    difference is that the log now names the cause.

    Deployment: set `FIREBASE_B2B_CREDENTIALS_JSON` to the service-account JSON
    itself. `FIREBASE_B2B_CREDENTIALS_FILE` works locally, but `certificates/`
    is in both `.gitignore` and `.dockerignore` — the path it names does not
    exist inside the image.
    """
    app = getattr(settings, "FIREBASE_B2B_APP", None)
    if app is None:
        raise B2BFirebaseNotConfigured(
            "No B2B Firebase project is configured, so B2B push cannot be sent: "
            "the workspace app's FCM tokens are only addressable from the "
            "project that issued them. Set FIREBASE_B2B_CREDENTIALS_JSON to the "
            "B2B service-account JSON (a *_CREDENTIALS_FILE path is not enough "
            "in the container — certificates/ is excluded from the image)."
        )
    return app


def _mask_token(token: str | None) -> str:
    if not token:
        return "unknown"
    if len(token) <= 12:
        return token
    return f"{token[:8]}...{token[-4:]}"


class FCMService:
    @staticmethod
    def _deactivate_invalid_tokens(tokens: list[str]):
        if not tokens:
            return
        execute(
            """
            UPDATE public.users
            SET fcm_token = NULL,
                device_type = NULL,
                updated_at = NOW()
            WHERE fcm_token = ANY(%s)
            """,
            [tokens],
        )

    @staticmethod
    def send_to_tokens(
        tokens: list[str],
        title: str,
        body: str,
        data: dict | None = None,
        app=None,
        deactivate_invalid=None,
    ):
        """Send one message to many tokens.

        `app` names the Firebase app to send from, and `None` means the default
        one — which is what every consumer and partner send passes. B2B callers
        hand in `b2b_firebase_app()` instead, because their tokens come from a
        different Firebase project and are not addressable from this one.

        `deactivate_invalid` is what clears the tokens Firebase reports as dead,
        and it exists for the same reason `app` does: a consumer token lives in
        `public.users`, a workspace one in `b2b_employee`, and the default —
        [_deactivate_invalid_tokens] — only knows the first. Left unset, every
        consumer and partner send behaves exactly as it always has; B2B callers
        pass their own so a dead workspace token is actually cleared instead of
        being retried on every message forever.
        """
        normalized_data = data or {}
        if not tokens:
            logger.info(
                "FCM skipped: empty token list. title=%s data_keys=%s",
                title,
                sorted(normalized_data.keys()),
            )
            return None

        logger.info(
            "FCM send started. title=%s tokens_total=%s data_keys=%s token_previews=%s",
            title,
            len(tokens),
            sorted(normalized_data.keys()),
            [_mask_token(token) for token in tokens],
        )

        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=normalized_data,
            tokens=tokens,
        )
        try:
            response = messaging.send_each_for_multicast(message, app=app)
        except Exception:
            logger.exception(
                "FCM send failed before per-token response. title=%s tokens_total=%s data_keys=%s",
                title,
                len(tokens),
                sorted(normalized_data.keys()),
            )
            return None

        invalid_tokens: list[str] = []
        for idx, send_response in enumerate(response.responses):
            if send_response.success:
                logger.info(
                    "FCM token delivery succeeded. token=%s",
                    _mask_token(tokens[idx] if idx < len(tokens) else "unknown"),
                )
                continue

            token = tokens[idx] if idx < len(tokens) else "unknown"
            error_code = getattr(getattr(send_response, "exception", None), "code", None)
            error_message = str(getattr(send_response, "exception", "Unknown error"))

            logger.warning(
                "FCM token delivery failed. token=%s code=%s error=%s",
                _mask_token(token),
                error_code,
                error_message,
            )

            if error_code in {
                "registration-token-not-registered",
                "invalid-registration-token",
                "invalid-argument",
                "unregistered",
            }:
                invalid_tokens.append(token)

        if invalid_tokens:
            deactivate = deactivate_invalid or FCMService._deactivate_invalid_tokens
            try:
                deactivate(invalid_tokens)
            except Exception:
                # The message went out; failing to tidy up after it must not
                # turn a partly-successful send into a raised exception.
                logger.exception(
                    "FCM invalid tokens could not be deactivated: count=%s",
                    len(invalid_tokens),
                )
            else:
                logger.info("FCM invalid tokens deactivated: count=%s", len(invalid_tokens))

        logger.info(
            "FCM send finished. title=%s success=%s failure=%s tokens_total=%s invalidated=%s",
            title,
            response.success_count,
            response.failure_count,
            len(tokens),
            len(invalid_tokens),
        )
        return response


class NotificationService:
    @staticmethod
    def _normalize_data(data: dict | None) -> dict:
        if not data:
            return {}
        normalized: dict[str, str] = {}
        for key, value in data.items():
            if value is None:
                continue
            normalized[str(key)] = str(value)
        return normalized

    @staticmethod
    def send_to_client(
        client,
        title: str,
        message: str,
        notification_type: str,
        data: dict | None = None,
    ):
        normalized_data = NotificationService._normalize_data(data)

        logger.info(
            "Client notification requested. client_id=%s notification_type=%s title=%s data_keys=%s",
            getattr(client, "id", None),
            notification_type,
            title,
            sorted(normalized_data.keys()),
        )

        notification = create_notification(
            recipient_user_id=getattr(client, "id", None),
            recipient_role="client",
            title=title,
            push_message=message,
            notification_type=notification_type,
            status="pending",
            is_for_every_one=False,
            payload=dict(data) if data else None,
        )

        tokens: list[str] = []
        token_rows = fetch_all(
            """
            SELECT fcm_token
            FROM public.users
            WHERE id = %s
              AND role = 'client'
              AND fcm_token IS NOT NULL
            """,
            [getattr(client, "id", None)],
        )
        tokens = [row["fcm_token"] for row in token_rows]

        logger.info(
            "Client notification tokens loaded. client_id=%s tokens_total=%s",
            getattr(client, "id", None),
            len(tokens),
        )

        response = FCMService.send_to_tokens(
            tokens=tokens,
            title=title,
            body=message,
            data=normalized_data,
        )

        if notification and response and response.success_count > 0:
            execute(
                """
                UPDATE public.notification
                SET status = 'sent'
                WHERE id = %s
                """,
                [notification["id"]],
            )
            logger.info(
                "Client notification marked sent. client_id=%s notification_id=%s success=%s failure=%s",
                getattr(client, "id", None),
                notification.get("id"),
                response.success_count,
                response.failure_count,
            )
        else:
            logger.warning(
                "Notification remains pending: no successful FCM delivery. client_id=%s notification_id=%s",
                getattr(client, "id", None),
                notification.get("id") if notification else None,
            )

        return notification

    @staticmethod
    def send_to_partner(
        partner,
        title: str,
        message: str,
        notification_type: str = "system",
        data: dict | None = None,
    ):
        """Send notification to partner and save to history"""
        normalized_data = NotificationService._normalize_data(data)
        logger.info(
            "Partner notification requested. partner_id=%s notification_type=%s title=%s data_keys=%s",
            getattr(partner, "id", None),
            notification_type,
            title,
            sorted(normalized_data.keys()),
        )

        notification = create_notification(
            recipient_user_id=getattr(partner, "id", None),
            recipient_role="partner",
            title=title,
            push_message=message,
            notification_type=notification_type,
            status="pending",
            is_for_every_one=False,
            payload=dict(data) if data else None,
        )
        logger.info(
            "Partner notification saved to normalized table. partner=%s title=%s",
            getattr(partner, "id", None),
            title,
        )

        # Send push notification
        tokens: list[str] = []
        token_rows = fetch_all(
            """
            SELECT fcm_token
            FROM public.users
            WHERE id = %s
              AND role = 'partner'
              AND fcm_token IS NOT NULL
            """,
            [getattr(partner, "id", None)],
        )
        tokens = [row["fcm_token"] for row in token_rows]

        logger.info(
            "Partner notification tokens loaded. partner_id=%s tokens_total=%s",
            getattr(partner, "id", None),
            len(tokens),
        )

        response = FCMService.send_to_tokens(
            tokens=tokens,
            title=title,
            body=message,
            data=normalized_data,
        )
        if response:
            logger.info(
                "Partner notification send result. partner_id=%s success=%s failure=%s",
                getattr(partner, "id", None),
                response.success_count,
                response.failure_count,
            )
        else:
            logger.warning(
                "Partner notification send skipped or produced no response. partner_id=%s",
                getattr(partner, "id", None),
            )

        if notification and response and response.success_count > 0:
            execute(
                """
                UPDATE public.notification
                SET status = 'sent'
                WHERE id = %s
                """,
                [notification["id"]],
            )
            logger.info(
                "Partner notification marked sent. partner_id=%s notification_id=%s success=%s failure=%s",
                getattr(partner, "id", None),
                notification.get("id"),
                response.success_count,
                response.failure_count,
            )

        return notification

    @staticmethod
    def send_broadcast(notification):
        message = messaging.send(
            messaging.Message(
                topic="all_clients",
                notification=messaging.Notification(
                    title=getattr(notification, "title", None),
                    body=getattr(notification, "push_message", None),
                ),
                data={
                    "type": "system",
                },
            )
        )
        logger.info("Response: %s", message)
