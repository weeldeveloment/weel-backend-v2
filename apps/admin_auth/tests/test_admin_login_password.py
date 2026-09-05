"""Admin sign-in actually verifies a password.

It did not. `AdminLoginSerializer` compared against a single shared `ADMIN_LOGIN_PASSWORD`
env var and, when that was unset — the deployed state — skipped the check altogether, so
any string signed in any active admin. `AdminCreateSerializer` compounded it by accepting
a password and discarding it, leaving every account with no hash to check against.

What is pinned here: the account's own hash decides, a wrong password is refused, an
account with no hash cannot sign in on nothing, and the shared variable survives only as
an explicit migration fallback for those accounts.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from django.contrib.auth.hashers import make_password
from rest_framework import serializers

from apps.admin_auth.serializers import AdminLoginSerializer

ADMIN = SimpleNamespace(id=7, role="admin", is_active=True, email="admin@weel.uz")
REAL_PASSWORD = "correct-horse-battery"
STORED_HASH = make_password(REAL_PASSWORD)

BY_EMAIL = "apps.admin_auth.serializers.get_active_admin_by_email"
HASH_FOR = "apps.admin_auth.serializers.get_admin_password_hash"


def _login(password, *, stored=STORED_HASH, user=ADMIN, shared=None, monkeypatch=None):
    if monkeypatch is not None:
        if shared is None:
            monkeypatch.delenv("ADMIN_LOGIN_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("ADMIN_LOGIN_PASSWORD", shared)
    with patch(BY_EMAIL, return_value=user), patch(HASH_FOR, return_value=stored):
        serializer = AdminLoginSerializer(data={"email": "admin@weel.uz", "password": password})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["user"]


def test_the_right_password_signs_in(monkeypatch):
    assert _login(REAL_PASSWORD, monkeypatch=monkeypatch) is ADMIN


def test_a_wrong_password_is_refused(monkeypatch):
    """The whole point: this returned a token before."""
    with pytest.raises(serializers.ValidationError):
        _login("x", monkeypatch=monkeypatch)


@pytest.mark.parametrize("attempt", ["", " ", "whatever", "................", REAL_PASSWORD + "!"])
def test_near_misses_and_junk_are_all_refused(attempt, monkeypatch):
    with pytest.raises(serializers.ValidationError):
        _login(attempt, monkeypatch=monkeypatch)


def test_the_stored_hash_wins_over_the_shared_variable(monkeypatch):
    """An account with its own password must not also accept the migration fallback."""
    with pytest.raises(serializers.ValidationError):
        _login("shared-one", stored=STORED_HASH, shared="shared-one", monkeypatch=monkeypatch)
    assert _login(REAL_PASSWORD, stored=STORED_HASH, shared="shared-one", monkeypatch=monkeypatch) is ADMIN


def test_an_account_with_no_password_cannot_sign_in_on_nothing(monkeypatch):
    """No hash and no fallback configured — the previous behaviour let anyone through."""
    with pytest.raises(serializers.ValidationError):
        _login("anything", stored="", monkeypatch=monkeypatch)


def test_the_shared_variable_still_migrates_an_old_account(monkeypatch):
    assert _login("legacy-shared", stored="", shared="legacy-shared", monkeypatch=monkeypatch) is ADMIN
    with pytest.raises(serializers.ValidationError):
        _login("wrong", stored="", shared="legacy-shared", monkeypatch=monkeypatch)


def test_an_unknown_email_is_refused_the_same_way(monkeypatch):
    with pytest.raises(serializers.ValidationError):
        _login(REAL_PASSWORD, user=None, monkeypatch=monkeypatch)


def test_a_new_admins_password_is_actually_stored():
    """It was accepted and dropped, which is why no account had a hash to check."""
    from apps.admin_auth.serializers import AdminCreateSerializer

    with patch("apps.admin_auth.serializers.exists_admin_email", return_value=False), \
         patch("apps.admin_auth.serializers.make_unique_admin_username", return_value="boss"), \
         patch("apps.admin_auth.serializers.create_admin_user") as create:
        serializer = AdminCreateSerializer(data={"email": "boss@weel.uz", "password": "a-real-password"})
        assert serializer.is_valid(), serializer.errors
        serializer.save()

    assert create.call_args.kwargs["password"] == "a-real-password"
