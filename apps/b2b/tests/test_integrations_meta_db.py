"""Whose Facebook page is whose, against a live PostgreSQL database.

The webhook routes a lead by the page it names, so the page row *is* the
boundary between two companies' funnels. `upsert_page` used to re-point a
page at whichever company connected it last — one marketer administering
pages for two clients, connecting in the second client's workspace, moved the
first client's customers onto the second one's board. The rule that stops it
lives in the SQL statement, so only the real schema can check it:

    WEEL_INTEGRATION_DB=1 \\
    DJANGO_SETTINGS_MODULE=core.settings \\
    DB_NAME=weel_test DB_HOST=127.0.0.1 \\
    pytest apps/b2b/tests/test_integrations_meta_db.py

Point DB_NAME at a throwaway database — never at the one serving traffic.
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("WEEL_INTEGRATION_DB") != "1",
        reason=(
            "Needs a live PostgreSQL database with the raw-SQL schema. "
            "Set WEEL_INTEGRATION_DB=1 and point DB_NAME at a throwaway database."
        ),
    ),
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture
def two_companies(django_db_setup, django_db_blocker):
    """Alfa and Beta, each with a live Meta connection."""
    from shared.raw.db import execute, fetch_one
    from apps.b2b.integrations import repository as int_repo

    with django_db_blocker.unblock():
        made = {}
        for name in ("Alfa Meta Co", "Beta Meta Co"):
            execute(
                "INSERT INTO b2b_company (name, is_active, created_at, updated_at) "
                "VALUES (%s, TRUE, NOW(), NOW())",
                [name],
            )
            company = fetch_one(
                "SELECT id FROM b2b_company WHERE name = %s ORDER BY id DESC LIMIT 1",
                [name],
            )
            integration = int_repo.upsert_integration(
                company_id=company["id"],
                account_id=f"fb-{company['id']}",
                account_name=name,
                access_token_enc="enc-user-token",
                connected_by_id=None,
            )
            made[name.split()[0].lower()] = {
                "company_id": company["id"], "integration_id": integration["id"],
            }
        yield made


def _put(int_repo, holder, page_id, name="Sahifa"):
    return int_repo.upsert_page(
        integration_id=holder["integration_id"],
        company_id=holder["company_id"],
        page_id=page_id,
        page_name=name,
        access_token_enc="enc-page-token",
        subscribed=True,
    )


def test_a_page_another_company_holds_is_not_taken(two_companies, django_db_blocker):
    from apps.b2b.integrations import repository as int_repo

    alfa, beta = two_companies["alfa"], two_companies["beta"]
    with django_db_blocker.unblock():
        assert _put(int_repo, alfa, "PG-1")["company_id"] == alfa["company_id"]

        assert int_repo.page_held_elsewhere("PG-1", beta["company_id"])
        assert _put(int_repo, beta, "PG-1") is None

        # Still Alfa's — which is where the webhook will send its leads.
        assert int_repo.find_page("PG-1")["company_id"] == alfa["company_id"]


def test_reconnecting_one_s_own_page_still_updates_it(two_companies, django_db_blocker):
    from apps.b2b.integrations import repository as int_repo

    alfa = two_companies["alfa"]
    with django_db_blocker.unblock():
        _put(int_repo, alfa, "PG-2", "Eski nom")
        int_repo.set_page_active(
            int_repo.find_page("PG-2")["id"], alfa["company_id"], False,
        )
        row = _put(int_repo, alfa, "PG-2", "Yangi nom")
        assert row["page_name"] == "Yangi nom"
        # A page the owner paused stays paused through a reconnect.
        assert row["is_active"] is False
        assert not int_repo.page_held_elsewhere("PG-2", alfa["company_id"])


def test_a_page_whose_company_disconnected_is_free(two_companies, django_db_blocker):
    from apps.b2b.integrations import repository as int_repo
    from apps.b2b.models import IntegrationProvider

    alfa, beta = two_companies["alfa"], two_companies["beta"]
    with django_db_blocker.unblock():
        _put(int_repo, alfa, "PG-3")
        # `disconnect` clears the token but — unlike the view — leaves the
        # page rows; nothing flows through them any more.
        int_repo.disconnect(alfa["company_id"], IntegrationProvider.META)

        assert not int_repo.page_held_elsewhere("PG-3", beta["company_id"])
        assert _put(int_repo, beta, "PG-3")["company_id"] == beta["company_id"]


def test_a_reconnect_drops_the_pages_it_no_longer_covers(two_companies, django_db_blocker):
    from apps.b2b.integrations import repository as int_repo

    alfa, beta = two_companies["alfa"], two_companies["beta"]
    with django_db_blocker.unblock():
        for page_id in ("PG-4", "PG-5"):
            _put(int_repo, alfa, page_id)
        _put(int_repo, beta, "PG-6")

        int_repo.delete_pages_except(alfa["integration_id"], ["PG-4"])
        assert [p["page_id"] for p in int_repo.list_pages(alfa["company_id"])] == ["PG-4"]

        int_repo.delete_pages_except(alfa["integration_id"], [])
        assert int_repo.list_pages(alfa["company_id"]) == []
        # Another company's pages are never in reach of it.
        assert [p["page_id"] for p in int_repo.list_pages(beta["company_id"])] == ["PG-6"]
