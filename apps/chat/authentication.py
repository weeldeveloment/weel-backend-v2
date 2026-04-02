import logging

from django.utils.translation import gettext_lazy as _

from rest_framework import exceptions
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import Token

from users.raw_repository import get_active_user_by_subject
from users.tokens import TokenMetadata


class RawAdminJWTAuthentication(JWTAuthentication):
    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            raise exceptions.AuthenticationFailed(
                _("Invalid authorization header"), code="bad_authorization_header"
            )

        validated_token = self.get_validated_token(raw_token)
        user_type = validated_token.get(TokenMetadata.TOKEN_USER_TYPE)
        if user_type != "admin":
            return None

        user = self.get_user(validated_token)
        return user, validated_token

    def get_user(self, validated_token: Token):
        try:
            subject = validated_token.get(TokenMetadata.TOKEN_SUBJECT)
            if not subject:
                raise exceptions.AuthenticationFailed(
                    _("Invalid admin token"), code="invalid_admin_token"
                )

            admin = get_active_user_by_subject(subject, role="admin")
            if admin is None:
                raise exceptions.AuthenticationFailed(
                    _("Admin user not found"), code="admin_not_found"
                )

            return admin
        except exceptions.AuthenticationFailed:
            raise
        except Exception:
            logging.exception("Raw admin authentication failed")
            raise exceptions.AuthenticationFailed(
                _("Authentication failed"), code="authentication_failed"
            )
