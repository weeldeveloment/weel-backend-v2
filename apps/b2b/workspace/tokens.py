from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import AccessToken

from shared import token_denylist
from users.tokens import CustomRefreshToken, TokenMetadata

# Distinct from the dashboard's "b2b" token: the subject is a
# ``b2b_employee`` id, not a ``b2b_user`` id, so the two must never be
# interchangeable — a dashboard token pointed at employee-scoped endpoints
# would resolve to whichever employee happens to share that primary key.
WORKSPACE_USER_TYPE = "b2b_employee"

# The account session — see `accounts.py`. Its subject is a ``b2b_account`` id,
# and it deliberately carries no company: somebody who has just registered
# belongs to nothing yet, and the endpoints this token opens are only the ones
# that get them somewhere. Kept a separate type so it can never be mistaken
# for a workspace token by an endpoint that expects an employee id.
WORKSPACE_ACCOUNT_TYPE = "b2b_account"

_CLAIMS = (
    TokenMetadata.TOKEN_SUBJECT,
    TokenMetadata.TOKEN_ISSUER,
    TokenMetadata.TOKEN_USER_TYPE,
    "company_id",
    "role",
)


def create_workspace_tokens(employee: dict[str, Any]) -> dict[str, str]:
    refresh = CustomRefreshToken()
    access = AccessToken()

    claims = {
        TokenMetadata.TOKEN_SUBJECT: str(employee["id"]),
        TokenMetadata.TOKEN_ISSUER: getattr(settings, "JWT_ISSUER", "weel-backend"),
        TokenMetadata.TOKEN_USER_TYPE: WORKSPACE_USER_TYPE,
        "company_id": employee["company_id"],
        "role": employee.get("role") or "employee",
    }
    for key, value in claims.items():
        refresh[key] = value
        access[key] = value

    refresh[TokenMetadata.TOKEN_TYPE_CLAIM] = "refresh"
    access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"

    return {"refresh": str(refresh), "access": str(access)}


def create_account_tokens(account: dict[str, Any]) -> dict[str, str]:
    """A session that knows who somebody is and nothing about where they work."""
    refresh = CustomRefreshToken()
    access = AccessToken()

    claims = {
        TokenMetadata.TOKEN_SUBJECT: str(account["id"]),
        TokenMetadata.TOKEN_ISSUER: getattr(settings, "JWT_ISSUER", "weel-backend"),
        TokenMetadata.TOKEN_USER_TYPE: WORKSPACE_ACCOUNT_TYPE,
    }
    for key, value in claims.items():
        refresh[key] = value
        access[key] = value

    refresh[TokenMetadata.TOKEN_TYPE_CLAIM] = "refresh"
    access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"

    return {"refresh": str(refresh), "access": str(access)}


def rotate_workspace_tokens(refresh_token: str) -> dict[str, str]:
    token = CustomRefreshToken(token=refresh_token)

    if token.get(TokenMetadata.TOKEN_USER_TYPE) != WORKSPACE_USER_TYPE:
        raise InvalidToken("Not a B2B workspace refresh token.")

    token_denylist.assert_not_revoked(token.payload)

    new_refresh = CustomRefreshToken()
    new_access = AccessToken()
    for claim in _CLAIMS:
        if claim in token:
            new_refresh[claim] = token[claim]
            new_access[claim] = token[claim]

    new_refresh[TokenMetadata.TOKEN_TYPE_CLAIM] = "refresh"
    new_access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"

    token.blacklist()

    return {"refresh": str(new_refresh), "access": str(new_access)}
