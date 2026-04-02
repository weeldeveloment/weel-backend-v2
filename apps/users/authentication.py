import logging
from django.utils.translation import gettext_lazy as _

from rest_framework import exceptions
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import Token

from .tokens import TokenMetadata
from .raw_repository import get_active_user_by_subject


class ClientJWTAuthentication(JWTAuthentication):
    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            # Header exists but doesn't match expected format (Bearer <token>)
            raise exceptions.AuthenticationFailed(
                _("Invalid authorization header"), code="bad_authorization_header"
            )

        validated_token = self.get_validated_token(raw_token)

        user_type = validated_token.get("user_type")
        if user_type != "client":
            return None

        user = self.get_user(validated_token)
        return user, validated_token

    def get_user(self, validated_token):
        try:
            subject = validated_token.get(TokenMetadata.TOKEN_SUBJECT)
            if not subject:
                return None

            client = get_active_user_by_subject(subject, role="client")
            if client is None:
                raise exceptions.AuthenticationFailed(
                    _("Client not found"), code="client_not_found"
                )
            return client
        except exceptions.AuthenticationFailed:
            raise
        except Exception as e:
            logging.exception(e)
            raise exceptions.AuthenticationFailed(
                _("Authentication failed"), code="authentication_failed"
            )


class PartnerJWTAuthentication(JWTAuthentication):
    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            # Header exists but doesn't match expected format (Bearer <token>)
            raise exceptions.AuthenticationFailed(
                _("Invalid authorization header"), code="bad_authorization_header"
            )

        validated_token = self.get_validated_token(raw_token)

        user_type = validated_token.get("user_type")
        if user_type != "partner":
            return None

        user = self.get_user(validated_token)
        return user, validated_token

    def get_user(self, validated_token: Token):
        try:
            subject = validated_token.get(TokenMetadata.TOKEN_SUBJECT)
            if not subject:
                return None

            partner = get_active_user_by_subject(subject, role="partner")
            if partner is None:
                raise exceptions.AuthenticationFailed(
                    _("Partner not found"), code="partner_not_found"
                )
            return partner
        except exceptions.AuthenticationFailed:
            raise
        except Exception as e:
            logging.exception(e)
            raise exceptions.AuthenticationFailed(
                _("Authentication failed"), code="authentication_failed"
            )


class ClientOrPartnerJWTAuthentication(JWTAuthentication):
    """
    Try Client JWT first, then Partner JWT.
    Use for endpoints that accept either client or partner token.
    """

    def authenticate(self, request: Request):
        client_auth = ClientJWTAuthentication()
        result = client_auth.authenticate(request)
        if result is not None:
            return result
        partner_auth = PartnerJWTAuthentication()
        return partner_auth.authenticate(request)
