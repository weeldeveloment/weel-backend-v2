"""The same workspace twice in somebody's switcher, against a live PostgreSQL
database.

Two halves: the doors that let a second copy in stay shut
(`accounts.create_membership`, `accounts.create_workspace`), and the copies
already made are put back together (`dedupe`). Both lean on the real schema
— the merge moves rows along every foreign key onto `b2b_employee`, and the
unique indexes on those tables are the whole difficulty — so this runs
against one:

    WEEL_INTEGRATION_DB=1 \\
    DJANGO_SETTINGS_MODULE=core.settings \\
    DB_NAME=weel_test DB_HOST=127.0.0.1 \\
    pytest apps/b2b/tests/test_workspace_dedupe_db.py

Point DB_NAME at a throwaway database — never at the one serving traffic.
"""
from __future__ import annotations

import os
from itertools import count

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

_phones = count(1)


@pytest.fixture
def db(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock():
        yield


def _account(first_name="Aziz"):
    from apps.b2b.workspace import accounts

    return accounts.ensure_account(
        f"+99890{next(_phones):07d}", first_name=first_name, last_name="Karimov"
    )


def _row(sql, params=()):
    from shared.raw.db import fetch_one

    return fetch_one(sql, list(params))


def _rows(sql, params=()):
    from shared.raw.db import fetch_all

    return fetch_all(sql, list(params))


def _exec(sql, params=()):
    from shared.raw.db import execute

    execute(sql, list(params))


def _second_seat(employee, **flags):
    """What the old doors wrote: another live row for the same person in
    the same workspace, the way the dashboard's employee form or a second
    accepted request did."""
    columns = {"is_chat_only": False, "is_guest": False, "role": "employee", **flags}
    return _row(
        """
        INSERT INTO b2b_employee
            (company_id, account_id, full_name, phone, role, is_active,
             is_chat_only, is_guest, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s, NOW(), NOW())
        RETURNING *
        """,
        [employee["company_id"], employee["account_id"], employee["full_name"],
         employee["phone"], columns["role"], columns["is_chat_only"], columns["is_guest"]],
    )


def _task(company_id, author_id, title="Hisobot"):
    return _row(
        "INSERT INTO b2b_task (company_id, title, author_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, NOW(), NOW()) RETURNING *",
        [company_id, title, author_id],
    )


def _switcher(account_id):
    from apps.b2b.workspace import accounts

    return [row["company_id"] for row in accounts.list_memberships(account_id)]


# ─── The doors ────────────────────────────────────────────────────────────────

def test_a_second_door_into_the_same_workspace_hands_back_the_seat_there(db):
    from apps.b2b.workspace import accounts

    aziz = _account()
    created = accounts.create_workspace(account=aziz, name="Aziz savdo")
    company_id = created["company"]["id"]

    again = accounts.create_membership(account=aziz, company_id=company_id, role="employee")

    assert again["id"] == created["employee"]["id"]
    assert again["role"] == "owner"
    assert _switcher(aziz["id"]) == [company_id]


def test_a_real_invitation_promotes_a_chat_only_seat_instead_of_adding_one(db):
    from apps.b2b.workspace import accounts

    owner = _account("Nodir")
    company_id = accounts.create_workspace(account=owner, name="Nodir savdo")["company"]["id"]
    guest = _account("Laylo")
    chat_seat = accounts.create_membership(
        account=guest, company_id=company_id, role="employee", is_chat_only=True
    )

    full = accounts.create_membership(
        account=guest, company_id=company_id, role="manager", modules=["chat", "tasks"]
    )

    assert full["id"] == chat_seat["id"]
    assert full["is_chat_only"] is False
    assert full["role"] == "manager"
    assert _switcher(guest["id"]) == [company_id]


@pytest.mark.parametrize("typed", ["Filial", "  filial ", "FILIAL"])
def test_a_workspace_named_like_one_in_the_company_is_refused(db, typed):
    from apps.b2b.workspace import accounts

    owner = _account("Nodir")
    first = accounts.create_workspace(account=owner, name="Bosh ofis")
    org_id = first["org"]["id"]
    accounts.create_workspace(account=owner, name="Filial", org_id=org_id)

    with pytest.raises(accounts.NameTaken) as taken:
        accounts.create_workspace(account=owner, name=typed, org_id=org_id)

    assert taken.value.kind == "workspace"
    names = _rows("SELECT name FROM b2b_company WHERE org_id = %s", [org_id])
    assert sorted(r["name"] for r in names) == ["Filial", "Sotuv bo'limi"]


def test_opening_the_same_company_twice_is_refused(db):
    from apps.b2b.workspace import accounts

    owner = _account("Nodir")
    accounts.create_workspace(account=owner, name="Chindan Group")

    with pytest.raises(accounts.NameTaken) as taken:
        accounts.create_workspace(account=owner, name="chindan  group")

    assert taken.value.kind == "company"
    assert len(_switcher(owner["id"])) == 1


def test_somebody_elses_company_of_the_same_name_is_not_a_copy(db):
    from apps.b2b.workspace import accounts

    accounts.create_workspace(account=_account("Nodir"), name="Chindan Group")
    other = _account("Sardor")

    created = accounts.create_workspace(account=other, name="Chindan Group")

    assert created["employee"]["role"] == "owner"


def test_a_person_is_seated_through_any_row_of_theirs(db):
    from apps.b2b.workspace import accounts

    aziz = _account()
    home = accounts.create_workspace(account=aziz, name="Toshkent")
    host = accounts.create_workspace(
        account=aziz, name="Samarqand", org_id=home["org"]["id"]
    )

    # Asked for as "the Toshkent row" by Samarqand, where Aziz is staff too.
    assert accounts.person_seated_in(host["company"]["id"], home["employee"]["id"])
    stranger = accounts.create_workspace(account=_account("Sardor"), name="Buxoro")
    assert not accounts.person_seated_in(
        stranger["company"]["id"], home["employee"]["id"]
    )


# ─── Putting the copies back together ─────────────────────────────────────────

def test_two_seats_of_one_person_become_one_and_keep_everything(db):
    from apps.b2b.workspace import accounts, dedupe

    aziz = _account()
    created = accounts.create_workspace(account=aziz, name="Aziz savdo")
    company_id = created["company"]["id"]
    keeper = created["employee"]
    extra = _second_seat(keeper)
    assert _switcher(aziz["id"]) == [company_id, company_id]

    # Work done through the extra seat, some of it overlapping the kept one.
    task = _task(company_id, extra["id"])
    both = _task(company_id, keeper["id"], "Ikkalasiga")
    for employee_id in (keeper["id"], extra["id"]):
        _exec("INSERT INTO b2b_task_assignee (task_id, employee_id) VALUES (%s, %s)",
              [both["id"], employee_id])
    thread = _row(
        "INSERT INTO b2b_chat_thread (company_id, kind, created_at, updated_at) "
        "VALUES (%s, 'group', NOW(), NOW()) RETURNING *",
        [company_id],
    )
    for employee_id in (keeper["id"], extra["id"]):
        _exec("INSERT INTO b2b_chat_member (thread_id, employee_id) VALUES (%s, %s)",
              [thread["id"], employee_id])
    _exec("INSERT INTO b2b_chat_message (thread_id, sender_id, text) VALUES (%s, %s, 'salom')",
          [thread["id"], extra["id"]])

    report = dedupe.run(log=lambda _line: None)

    assert report["seats_merged"] == 1
    assert _switcher(aziz["id"]) == [company_id]
    assert _row("SELECT is_active FROM b2b_employee WHERE id = %s", [extra["id"]])["is_active"] is False
    assert _row("SELECT author_id FROM b2b_task WHERE id = %s", [task["id"]])["author_id"] == keeper["id"]
    assert [r["employee_id"] for r in _rows(
        "SELECT employee_id FROM b2b_task_assignee WHERE task_id = %s", [both["id"]]
    )] == [keeper["id"]]
    assert [r["employee_id"] for r in _rows(
        "SELECT employee_id FROM b2b_chat_member WHERE thread_id = %s", [thread["id"]]
    )] == [keeper["id"]]
    assert _row("SELECT sender_id FROM b2b_chat_message WHERE thread_id = %s",
                [thread["id"]])["sender_id"] == keeper["id"]

    # And a second start-up finds nothing left to do. (Only the seats are
    # asked about: the raw tables are not flushed between tests, and a copy
    # another test left "for a person" is reported again every run.)
    assert dedupe.run(log=lambda _line: None)["seats_merged"] == 0
    assert dedupe.duplicate_seat_groups() == []


def test_the_staff_seat_is_kept_over_a_guest_copy_and_the_loan_ends(db):
    from apps.b2b.workspace import accounts, dedupe

    aziz = _account()
    created = accounts.create_workspace(account=aziz, name="Aziz savdo")
    staff = created["employee"]
    guest = _second_seat(staff, is_guest=True, role="manager")
    _exec(
        """
        INSERT INTO b2b_workspace_membership
            (company_id, employee_id, home_employee_id, role, is_active)
        VALUES (%s, %s, %s, 'manager', TRUE)
        """,
        [staff["company_id"], guest["id"], staff["id"]],
    )

    dedupe.run(log=lambda _line: None)

    assert _row("SELECT is_active FROM b2b_employee WHERE id = %s", [staff["id"]])["is_active"] is True
    assert _row("SELECT is_active FROM b2b_employee WHERE id = %s", [guest["id"]])["is_active"] is False
    assert _row(
        "SELECT is_active FROM b2b_workspace_membership WHERE employee_id = %s", [guest["id"]]
    )["is_active"] is False


def test_an_empty_second_company_from_a_double_tap_is_retired(db):
    from apps.b2b.workspace import dedupe

    from apps.b2b.workspace import accounts

    owner = _account("Nodir")
    first = accounts.create_workspace(account=owner, name="Chindan Group")
    _task(first["company"]["id"], first["employee"]["id"])
    # The second tap, as the old endpoint answered it.
    copy = accounts._open_workspace(
        account=owner, name="Chindan Group", org_id=None, description=None,
        icon=None, workspace_name=None, tax_id=None,
    )
    assert len(_switcher(owner["id"])) == 2

    report = dedupe.run(log=lambda _line: None)

    assert report["companies_retired"] == 1
    assert _switcher(owner["id"]) == [first["company"]["id"]]
    assert _row("SELECT is_active FROM b2b_org WHERE id = %s", [copy["org"]["id"]])["is_active"] is False
    assert _row("SELECT is_active FROM b2b_org WHERE id = %s", [first["org"]["id"]])["is_active"] is True


def test_an_empty_same_named_workspace_in_one_company_is_retired(db):
    from apps.b2b.workspace import accounts, dedupe

    owner = _account("Nodir")
    first = accounts.create_workspace(account=owner, name="Bosh ofis")
    org_id = first["org"]["id"]
    kept = accounts.create_workspace(account=owner, name="Filial", org_id=org_id)
    _task(kept["company"]["id"], kept["employee"]["id"])
    copy = accounts._open_workspace(
        account=owner, name="filial", org_id=org_id, description=None,
        icon=None, workspace_name=None, tax_id=None,
    )

    report = dedupe.run(log=lambda _line: None)

    assert report["workspaces_retired"] == 1
    assert _row("SELECT is_active FROM b2b_company WHERE id = %s",
                [copy["company"]["id"]])["is_active"] is False
    assert copy["company"]["id"] not in _switcher(owner["id"])
    assert kept["company"]["id"] in _switcher(owner["id"])


def test_copies_that_both_hold_work_are_left_for_a_person(db):
    from apps.b2b.workspace import accounts, dedupe

    owner = _account("Nodir")
    first = accounts.create_workspace(account=owner, name="Bosh ofis")
    org_id = first["org"]["id"]
    one = accounts.create_workspace(account=owner, name="Filial", org_id=org_id)
    two = accounts._open_workspace(
        account=owner, name="Filial", org_id=org_id, description=None,
        icon=None, workspace_name=None, tax_id=None,
    )
    _task(one["company"]["id"], one["employee"]["id"])
    _task(two["company"]["id"], two["employee"]["id"])

    lines = []
    report = dedupe.run(log=lines.append)

    assert report["workspaces_retired"] == 0
    assert report["left_for_a_person"] == 1
    assert any("left as they are" in line for line in lines)
    active = _rows("SELECT id FROM b2b_company WHERE org_id = %s AND is_active", [org_id])
    assert {one["company"]["id"], two["company"]["id"]} <= {r["id"] for r in active}


def test_an_empty_copy_somebody_else_opened_is_left_for_a_person(db):
    """Two people opening the same name is not a double tap: retiring the
    empty one would take the only seat its opener has in that room."""
    from apps.b2b.workspace import accounts, dedupe

    owner = _account("Nodir")
    first = accounts.create_workspace(account=owner, name="Bosh ofis")
    org_id = first["org"]["id"]
    kept = accounts.create_workspace(account=owner, name="Filial", org_id=org_id)
    _task(kept["company"]["id"], kept["employee"]["id"])
    admin = _account("Sardor")
    accounts.create_membership(account=admin, company_id=first["company"]["id"], role="admin")
    theirs = accounts._open_workspace(
        account=admin, name="Filial", org_id=org_id, description=None,
        icon=None, workspace_name=None, tax_id=None,
    )

    report = dedupe.run(log=lambda _line: None)

    assert report["workspaces_retired"] == 0
    assert theirs["company"]["id"] in _switcher(admin["id"])


def test_the_command_that_runs_on_every_start_up_does_the_cleanup(db):
    from django.core.management import call_command

    from apps.b2b.workspace import accounts

    aziz = _account()
    created = accounts.create_workspace(account=aziz, name="Aziz savdo")
    _second_seat(created["employee"])

    call_command("create_b2b_tables", verbosity=0)

    assert _switcher(aziz["id"]) == [created["company"]["id"]]
