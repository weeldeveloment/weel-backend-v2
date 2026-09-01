from __future__ import annotations

import os
from datetime import timedelta
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


# How long a phone may go untouched and still come back to a live session.
#
# A year, deliberately, and not the project-wide `REFRESH_TOKEN_LIFETIME` of a
# week: signing in on a phone is meant to be something a person does once.
# Every shorter window this had — seven days, then thirty — ended the same
# way, with somebody who had been away pushed back through phone and OTP for
# no reason they could see, and the app was the only thing that knew why.
#
# What keeps that from being a year-long credential handed out and forgotten:
#
#   * rotation. Every refresh mints a new token and revokes the one it
#     replaces (`rotate_workspace_tokens` below), so what a phone holds is
#     never more than one refresh old, and a token lifted off a device stops
#     working the moment the real phone next refreshes.
#   * revocation. Logging out, on this phone or from another, denylists the
#     token for the rest of its life — see `apps/shared/token_denylist.py`,
#     whose entries now outlive this window rather than expiring under it.
#   * the app lock. A year-long session is not a year of anybody who picks up
#     the phone being able to read it; that is the PIN, and it is a separate
#     thing.
#
# Only these two sessions are lengthened. The dashboard and admin tokens keep
# the short window, because a browser on a shared machine is not a phone.
WORKSPACE_REFRESH_LIFETIME = timedelta(
    days=int((os.getenv("B2B_WORKSPACE_REFRESH_DAYS") or "365").strip() or "365")
)


class WorkspaceRefreshToken(CustomRefreshToken):
    """A [CustomRefreshToken] carrying the mobile session window above."""

    lifetime = WORKSPACE_REFRESH_LIFETIME


_CLAIMS = (
    TokenMetadata.TOKEN_SUBJECT,
    TokenMetadata.TOKEN_ISSUER,
    TokenMetadata.TOKEN_USER_TYPE,
    "company_id",
    "role",
)


def create_workspace_tokens(employee: dict[str, Any]) -> dict[str, str]:
    refresh = WorkspaceRefreshToken()
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
    refresh = WorkspaceRefreshToken()
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

    new_refresh = WorkspaceRefreshToken()
    new_access = AccessToken()
    for claim in _CLAIMS:
        if claim in token:
            new_refresh[claim] = token[claim]
            new_access[claim] = token[claim]

    new_refresh[TokenMetadata.TOKEN_TYPE_CLAIM] = "refresh"
    new_access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"

    token.blacklist()

    return {"refresh": str(new_refresh), "access": str(new_access)}


def rotate_account_tokens(refresh_token: str) -> dict[str, str]:
    """The account session's own rotation.

    Separate from `rotate_workspace_tokens` for the reason the two types exist
    at all: neither endpoint may hand back a token of the other's type, and a
    single view that accepted both would be one `if` away from doing exactly
    that. The account session was left without any rotation at all, which made
    it expire for good after one access lifetime — somebody who registered and
    was waiting on a join request simply lost the ability to list their
    workspaces an hour later, with no way back short of signing in again.
    """
    token = CustomRefreshToken(token=refresh_token)

    if token.get(TokenMetadata.TOKEN_USER_TYPE) != WORKSPACE_ACCOUNT_TYPE:
        raise InvalidToken("Not a B2B account refresh token.")

    token_denylist.assert_not_revoked(token.payload)

    new_refresh = WorkspaceRefreshToken()
    new_access = AccessToken()
    for claim in _CLAIMS:
        if claim in token:
            new_refresh[claim] = token[claim]
            new_access[claim] = token[claim]

    new_refresh[TokenMetadata.TOKEN_TYPE_CLAIM] = "refresh"
    new_access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"

    token.blacklist()

    return {"refresh": str(new_refresh), "access": str(new_access)}
