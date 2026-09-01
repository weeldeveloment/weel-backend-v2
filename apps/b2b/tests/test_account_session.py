"""The account session's own lifetime.

The workspace session has always been able to renew itself; the account
session — the one somebody holds between registering and being let into a
workspace — could not. It expired one access lifetime after sign-in and there
was no endpoint that would take its refresh token, so the workspace picker
started answering "could not load your workspaces" with a Retry button that
could never succeed.

What is pinned here is that the two rotations stay separate: each takes its
own kind of token, refuses the other's, and hands back a token of the type it
was given.
"""
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework.test import APIRequestFactory

from shared import token_denylist
from apps.b2b.workspace.tokens import (
    WORKSPACE_ACCOUNT_TYPE,
    WORKSPACE_REFRESH_LIFETIME,
    WORKSPACE_USER_TYPE,
    create_account_tokens,
    create_workspace_tokens,
    rotate_account_tokens,
    rotate_workspace_tokens,
)
from apps.b2b.workspace.views import AccountTokenRefreshView
from users.tokens import CustomRefreshToken, TokenMetadata

factory = APIRequestFactory()

ACCOUNT = {"id": 42}
EMPLOYEE = {"id": 7, "company_id": 3, "role": "employee"}


def _type_of(raw: str) -> str:
    return CustomRefreshToken(token=raw).get(TokenMetadata.TOKEN_USER_TYPE)


def test_an_account_refresh_token_can_be_rotated():
    tokens = create_account_tokens(ACCOUNT)

    rotated = rotate_account_tokens(tokens["refresh"])

    assert rotated["access"] and rotated["refresh"]
    assert rotated["refresh"] != tokens["refresh"]
    # The subject and the type survive: a rotated token still authenticates as
    # the same account, and never as an employee with that primary key.
    assert _type_of(rotated["refresh"]) == WORKSPACE_ACCOUNT_TYPE
    new = CustomRefreshToken(token=rotated["refresh"])
    assert new.get(TokenMetadata.TOKEN_SUBJECT) == str(ACCOUNT["id"])


def test_the_two_rotations_refuse_each_others_tokens():
    account = create_account_tokens(ACCOUNT)["refresh"]
    workspace = create_workspace_tokens(EMPLOYEE)["refresh"]

    with pytest.raises(InvalidToken):
        rotate_workspace_tokens(account)

    with pytest.raises(InvalidToken):
        rotate_account_tokens(workspace)

    assert _type_of(rotate_workspace_tokens(workspace)["refresh"]) == WORKSPACE_USER_TYPE


def test_the_endpoint_hands_back_a_pair():
    tokens = create_account_tokens(ACCOUNT)

    response = AccountTokenRefreshView.as_view()(
        factory.post(
            "/account/token/refresh/", {"refresh": tokens["refresh"]}, format="json"
        )
    )

    assert response.status_code == 200
    assert set(response.data) == {"access", "refresh"}


def test_the_endpoint_refuses_a_workspace_token_rather_than_upgrading_it():
    workspace = create_workspace_tokens(EMPLOYEE)["refresh"]

    response = AccountTokenRefreshView.as_view()(
        factory.post("/account/token/refresh/", {"refresh": workspace}, format="json")
    )

    assert response.status_code == 401


def _days_to_expiry(raw: str) -> float:
    payload = CustomRefreshToken(token=raw).payload
    issued = payload[TokenMetadata.TOKEN_CREATED_TIME_CLAIM]
    expires = payload[TokenMetadata.TOKEN_EXPIRE_TIME_CLAIM]
    return (expires - issued) / 86400


@pytest.mark.parametrize(
    "mint",
    [
        pytest.param(lambda: create_workspace_tokens(EMPLOYEE), id="workspace"),
        pytest.param(lambda: create_account_tokens(ACCOUNT), id="account"),
        pytest.param(
            lambda: rotate_workspace_tokens(
                create_workspace_tokens(EMPLOYEE)["refresh"]
            ),
            id="workspace-rotated",
        ),
        pytest.param(
            lambda: rotate_account_tokens(create_account_tokens(ACCOUNT)["refresh"]),
            id="account-rotated",
        ),
    ],
)
def test_mobile_refresh_tokens_last_a_year(mint):
    """Both sessions, minted and rotated, are issued for a year.

    Signing in on a phone is meant to be something a person does once. Every
    shorter window ended with somebody who had been away pushed back through
    OTP for no reason they could see.

    Rotation is checked alongside minting because a rotated token that
    inherited the project default would move the wall rather than remove it:
    the session would still die a week after whichever refresh last happened,
    which is the same bug one refresh further along.
    """
    assert _days_to_expiry(mint()["refresh"]) == pytest.approx(365, abs=0.01)


def test_a_revoked_token_stays_revoked_for_its_whole_life():
    """The denylist entry has to outlive the token it revokes.

    `_remaining_seconds` clamps to `_MAX_TTL_SECONDS`, so that ceiling is not
    only a fallback for a token with no `exp` — it caps every entry. Left at
    31 days under a year-long token, a logout would have held for a month and
    then quietly stopped holding.
    """
    raw = create_workspace_tokens(EMPLOYEE)["refresh"]
    token = CustomRefreshToken(token=raw)

    assert token_denylist._remaining_seconds(token.payload) == pytest.approx(
        WORKSPACE_REFRESH_LIFETIME.total_seconds(), abs=60
    )
