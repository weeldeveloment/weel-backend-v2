"""Shared JWT authentication base.

Every authentication class in the project (client, partner, B2B, PMS) goes
through ``get_validated_token``, so checking the revocation denylist here
covers all of them from a single place.
"""

from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication

from shared import token_denylist


class DenylistCheckedJWTAuthentication(JWTAuthentication):
    def get_validated_token(self, raw_token):
        validated_token = super().get_validated_token(raw_token)
        token_denylist.assert_not_revoked(validated_token.payload)
        return validated_token
