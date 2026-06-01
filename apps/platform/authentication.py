from __future__ import annotations

import logging

from rest_framework import exceptions
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication

from users.raw_repository import get_user_by_id

logger = logging.getLogger(__name__)

PMS_USER_TYPE = "pms"


class PmsUser:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def is_authenticated(self):
        return True


class PmsJWTAuthentication(JWTAuthentication):
    def authenticate(self, request: Request):
        header = self.get_header(request)
        if header is None:
            return None

        raw_token = self.get_raw_token(header)
        if raw_token is None:
            raise exceptions.AuthenticationFailed("Invalid authorization header")

        validated_token = self.get_validated_token(raw_token)

        user_type = validated_token.get("user_type")
        if user_type != PMS_USER_TYPE:
            return None

        user = self.get_user(validated_token)
        return user, validated_token

    def get_user(self, validated_token):
        try:
            user_id = validated_token.get("sub")
            if not user_id:
                return None

            user = get_user_by_id(int(user_id), role="pms", active_only=True)
            if user is None:
                raise exceptions.AuthenticationFailed("User not found")

            return PmsUser(
                id=user.id,
                phone_number=user.phone_number,
                first_name=user.first_name,
                last_name=user.last_name,
                guid=str(user.guid),
                user_type=PMS_USER_TYPE,
                organization_id=validated_token.get("organization_id"),
            )
        except exceptions.AuthenticationFailed:
            raise
        except Exception as e:
            logger.exception(e)
            raise exceptions.AuthenticationFailed("Authentication failed")
