"""The handle somebody picks during registration, and where it has to land.

The handle lives on `b2b_account`, but every roster row keeps a copy of it so
listing a workspace needs no join. Two things have to be true for "@aziz" to
find Aziz: the copy has to be written when the handle is set, and the searches
have to fall back to the account for the rows written before it was.

Both were false. `PUT /account/me/` — the registration screen, and the one
most handles are set on — wrote only the account, so the copies stayed empty;
and the two searches that read a handle read only the copy. The picker on
"So'rov yuborish" invited "@foydalanuvchi nomi" in its own placeholder and
then matched nobody.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceAccount
from apps.b2b.workspace.joining_views import AccountMeView

factory = APIRequestFactory()

ACCOUNT_ID = 512
ACCOUNT = WorkspaceAccount({"id": ACCOUNT_ID, "phone": "+998901234567"})


def _save(username: str = "aziz"):
    request = factory.put(
        "/account/me/",
        {"first_name": "Aziz", "last_name": "Karimov", "username": username},
        format="json",
    )
    force_authenticate(request, user=ACCOUNT)
    with patch(
        "apps.b2b.workspace.joining_views.accounts.username_taken", return_value=False
    ), patch(
        "apps.b2b.workspace.joining_views.accounts.update_account",
        return_value={"id": ACCOUNT_ID, "username": username, "phone": "+998901234567"},
    ) as write, patch(
        "apps.b2b.workspace.joining_views.repo.sync_username_across_memberships"
    ) as fanout, patch(
        "apps.b2b.workspace.joining_views._workspaces", return_value=[]
    ):
        response = AccountMeView.as_view()(request)
    return response, write, fanout


def test_the_handle_reaches_the_roster_rows_not_only_the_account():
    response, write, fanout = _save()

    assert response.status_code == 200
    assert write.call_args.kwargs["username"] == "aziz"
    assert fanout.call_args.args == (ACCOUNT_ID, "aziz")


def test_the_at_sign_is_not_part_of_the_handle_that_is_stored():
    """It is how a handle is written and read; the column holds the name."""
    _, write, fanout = _save("@aziz")

    assert write.call_args.kwargs["username"] == "aziz"
    assert fanout.call_args.args == (ACCOUNT_ID, "aziz")


@pytest.mark.parametrize("column", ["e.username", "a.username"])
def test_both_searches_read_the_account_handle(column):
    """The SQL is what this is about: a roster row whose copy is empty must
    still be found by the handle on its account."""
    from apps.b2b.workspace.repository import list_team
    from apps.b2b.workspace.secondment_repository import search_org_people

    seen = []

    def capture(sql, params=None):
        seen.append(sql)
        return []

    with patch("apps.b2b.workspace.repository.fetch_all", side_effect=capture):
        list_team(1, search="@aziz")
    with patch(
        "apps.b2b.workspace.secondment_repository.fetch_all", side_effect=capture
    ):
        search_org_people(1, search="@aziz")

    assert len(seen) == 2
    for sql in seen:
        assert "COALESCE(a.username, e.username) ILIKE" in sql
        assert column in sql
