from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions
from rest_framework.request import Request
from shared.jwt_authentication import DenylistCheckedJWTAuthentication

from users.tokens import TokenMetadata
from apps.b2b.repository import get_b2b_user


class B2BAuthUser:
    """Lightweight wrapper so IsAuthenticated sees is_authenticated=True."""

    is_authenticated = True
    is_active = True

    def __init__(self, data: dict):
        self._data = data
        self.id = data.get("id")
        self.company_id = data.get("company_id")
        self.role = data.get("role", "performer")
        self.phone = data.get("phone")
        self.first_name = data.get("first_name")
        self.last_name = data.get("last_name")

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


class B2BJWTAuthentication(DenylistCheckedJWTAuthentication):
    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            return None

        validated_token = self.get_validated_token(raw_token)

        if validated_token.get(TokenMetadata.TOKEN_USER_TYPE) != "b2b":
            return None

        subject = validated_token.get(TokenMetadata.TOKEN_SUBJECT)
        if not subject:
            raise exceptions.AuthenticationFailed(_("Invalid B2B token"), code="invalid_b2b_token")

        try:
            user_id = int(subject)
        except (ValueError, TypeError):
            raise exceptions.AuthenticationFailed(_("Invalid B2B token subject"), code="invalid_b2b_token")

        b2b_user = get_b2b_user(user_id)
        if not b2b_user:
            raise exceptions.AuthenticationFailed(_("B2B user not found"), code="b2b_user_not_found")

        return B2BAuthUser(b2b_user), validated_token
