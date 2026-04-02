import logging

from firebase_admin import messaging

from shared.raw.db import execute, fetch_all, table_exists
from .raw_repository import create_notification


logger = logging.getLogger(__name__)


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
        if table_exists("client_devices"):
            execute(
                """
                UPDATE public.client_devices
                SET is_active = FALSE
                WHERE is_active = TRUE
                  AND fcm_token = ANY(%s)
                """,
                [tokens],
            )
        if table_exists("partner_devices"):
            execute(
                """
                UPDATE public.partner_devices
                SET is_active = FALSE
                WHERE is_active = TRUE
                  AND fcm_token = ANY(%s)
                """,
                [tokens],
            )

    @staticmethod
    def send_to_tokens(
        tokens: list[str], title: str, body: str, data: dict | None = None
    ):
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
            response = messaging.send_each_for_multicast(message)
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
            FCMService._deactivate_invalid_tokens(invalid_tokens)
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
        )

        tokens: list[str] = []
        if table_exists("client_devices"):
            token_rows = fetch_all(
                """
                SELECT fcm_token
                FROM public.client_devices
                WHERE client_id = %s
                  AND is_active = TRUE
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

        create_notification(
            recipient_user_id=getattr(partner, "id", None),
            recipient_role="partner",
            title=title,
            push_message=message,
            notification_type=notification_type,
            status="pending",
            is_for_every_one=False,
        )
        logger.info(
            "Partner notification saved to normalized table. partner=%s title=%s",
            getattr(partner, "id", None),
            title,
        )

        # Send push notification
        tokens: list[str] = []
        if table_exists("partner_devices"):
            token_rows = fetch_all(
                """
                SELECT fcm_token
                FROM public.partner_devices
                WHERE partner_id = %s
                  AND is_active = TRUE
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
        return response

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
