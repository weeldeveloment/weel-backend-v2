"""The SQL behind the Call Center Desk read model.

These queries have no ORM model to lean on and they aggregate across four tables per
row, so the parts worth pinning are the ones a reader cannot check by eye: that a
company's counts match the rows the desk lists when an agent clicks through, that the
org/workspace naming split is respected, and that guests and hidden people are excluded
from both sides of that comparison.

Runs against a live PostgreSQL database carrying the raw schema:

    WEEL_INTEGRATION_DB=1 \\
    DJANGO_SETTINGS_MODULE=core.settings \\
    DB_NAME=weel_test DB_HOST=127.0.0.1 \\
    pytest apps/admin_auth/tests/test_ccd_repository.py

Point DB_NAME at a throwaway database — never at the one serving traffic. The tests
create their own rows and clean them up, but they are not worth the risk.
"""
from __future__ import annotations

import os

import pytest

_needs_db = [
    pytest.mark.django_db,
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("WEEL_INTEGRATION_DB") != "1",
        reason=(
            "Needs PostgreSQL with the raw b2b_* schema. "
            "Set WEEL_INTEGRATION_DB=1 and point DB_NAME at a throwaway database."
        ),
    ),
]

pytestmark = _needs_db


@pytest.fixture
def desk_data():
    """One company, two workspaces, and a roster that includes the rows the desk hides."""
    from shared.raw.db import execute, fetch_one

    org = fetch_one(
        "INSERT INTO b2b_org (name, is_active, created_at, updated_at) "
        "VALUES ('CCD Test Org', TRUE, NOW(), NOW()) RETURNING id"
    )
    ws_a = fetch_one(
        "INSERT INTO b2b_company (name, legal_name, inn, city, org_id, is_active, created_at, updated_at) "
        "VALUES ('CCD WS A', 'CCD TEST MChJ', '123456789', 'Toshkent', %s, TRUE, NOW(), NOW()) RETURNING id",
        [org["id"]],
    )
    ws_b = fetch_one(
        "INSERT INTO b2b_company (name, org_id, is_active, created_at, updated_at) "
        "VALUES ('CCD WS B', %s, TRUE, NOW(), NOW()) RETURNING id",
        [org["id"]],
    )
    people = [
        ("CCD Active One", True, False, False),
        ("CCD Active Two", True, False, False),
        ("CCD Blocked", False, False, False),   # blocked, but still the desk's problem
        ("CCD Guest", True, True, False),       # lent in from elsewhere
        ("CCD Hidden", True, False, True),      # deliberately off rosters
    ]
    for name, active, guest, hidden in people:
        execute(
            "INSERT INTO b2b_employee (company_id, full_name, is_active, is_guest, is_hidden, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, NOW(), NOW())",
            [ws_a["id"], name, active, guest, hidden],
        )

    yield {"org_id": org["id"], "ws_a": ws_a["id"], "ws_b": ws_b["id"]}

    execute("DELETE FROM b2b_employee WHERE company_id IN (%s, %s)", [ws_a["id"], ws_b["id"]])
    execute("DELETE FROM b2b_company WHERE org_id = %s", [org["id"]])
    execute("DELETE FROM b2b_org WHERE id = %s", [org["id"]])


def _company(org_id):
    from apps.admin_auth import ccd_repository as repo

    return repo.get_company(org_id)


def test_a_company_is_an_org_not_a_workspace(desk_data):
    """The desk's "company" is a b2b_org; its two workspaces must not become two companies."""
    from apps.admin_auth import ccd_repository as repo

    company = _company(desk_data["org_id"])
    assert company["name"] == "CCD Test Org"
    assert company["workspaces"] == 2

    ids = [c["id"] for c in repo.list_companies(search="CCD Test Org")]
    assert ids == [desk_data["org_id"]]


def test_company_details_fall_back_to_the_primary_workspace(desk_data):
    """b2b_org carries no legal name, INN or city — they come off its first workspace."""
    company = _company(desk_data["org_id"])
    assert company["legal"] == "CCD TEST MChJ"
    assert company["inn"] == "123456789"
    assert company["city"] == "Toshkent"


def test_the_user_count_matches_the_roster(desk_data):
    """The number on the company row is the number of rows the agent then sees."""
    from apps.admin_auth import ccd_repository as repo

    company = _company(desk_data["org_id"])
    roster = repo.list_employees(org_id=desk_data["org_id"])
    assert company["users"] == len(roster) == 3


def test_guests_and_hidden_people_are_off_the_roster(desk_data):
    from apps.admin_auth import ccd_repository as repo

    names = {e["name"] for e in repo.list_employees(org_id=desk_data["org_id"])}
    assert "CCD Guest" not in names and "CCD Hidden" not in names


def test_a_blocked_person_is_still_listed(desk_data):
    """Blocking is a desk action; the person has to stay visible to be unblocked."""
    from apps.admin_auth import ccd_repository as repo

    roster = repo.list_employees(org_id=desk_data["org_id"])
    blocked = [e for e in roster if e["name"] == "CCD Blocked"]
    assert len(blocked) == 1 and blocked[0]["is_active"] is False


def test_the_roster_carries_both_names(desk_data):
    """An agent needs the company and the workspace, since one company has several."""
    from apps.admin_auth import ccd_repository as repo

    row = repo.list_employees(org_id=desk_data["org_id"])[0]
    assert row["company_name"] == "CCD Test Org"
    assert row["workspace_name"] == "CCD WS A"
    assert row["company_id"] == desk_data["org_id"]
    assert row["workspace_id"] == desk_data["ws_a"]


def test_search_matches_name_and_phone(desk_data):
    from apps.admin_auth import ccd_repository as repo
    from shared.raw.db import execute

    execute(
        "UPDATE b2b_employee SET phone = '+998901112233' WHERE full_name = 'CCD Active One'"
    )
    assert [e["name"] for e in repo.list_employees(search="CCD Active One")] == ["CCD Active One"]
    assert [e["name"] for e in repo.list_employees(search="901112233")] == ["CCD Active One"]


def test_workspaces_can_be_scoped_to_one_company(desk_data):
    from apps.admin_auth import ccd_repository as repo

    names = sorted(w["name"] for w in repo.list_workspaces(org_id=desk_data["org_id"]))
    assert names == ["CCD WS A", "CCD WS B"]
    members = {w["name"]: w["members"] for w in repo.list_workspaces(org_id=desk_data["org_id"])}
    assert members["CCD WS B"] == 0, "an empty workspace counts zero, not null"


def test_blocking_and_unblocking_round_trips(desk_data):
    from apps.admin_auth import ccd_repository as repo
    from shared.raw.db import fetch_one

    employee = fetch_one("SELECT id FROM b2b_employee WHERE full_name = 'CCD Active One'")
    assert repo.set_employee_active(employee["id"], active=False)["is_active"] is False
    assert repo.set_employee_active(employee["id"], active=True)["is_active"] is True


def test_blocking_an_unknown_id_returns_nothing(desk_data):
    from apps.admin_auth import ccd_repository as repo

    assert repo.set_employee_active(2_000_000_000, active=False) is None


def test_freezing_a_workspace_round_trips(desk_data):
    from apps.admin_auth import ccd_repository as repo

    assert repo.set_workspace_active(desk_data["ws_b"], active=False)["is_active"] is False
    assert repo.set_workspace_active(desk_data["ws_b"], active=True)["is_active"] is True


def test_the_read_only_lists_answer_without_error():
    """Calls, audit and the approval union have no fixture — they must still be valid SQL."""
    from apps.admin_auth import ccd_repository as repo

    assert isinstance(repo.list_calls(limit=5), list)
    assert isinstance(repo.list_audit(limit=5), list)
    assert isinstance(repo.list_approvals(), list)
