"""`created_by`/`requested_by`/`reviewed_by` are foreign keys into `b2b_user`.

The workspace apps sign in as an employee, whose id is a `b2b_employee` row
from an unrelated id space — writing it into those columns is a constraint
violation, which is how every hotel booking from the b2b mobile app died with
a 500 on the trip it creates first.
"""

from types import SimpleNamespace

from apps.b2b.views import _get_b2b_user_id, _get_user_id
from apps.b2b.workspace.authentication import WorkspaceUser


def _request(user):
    return SimpleNamespace(user=user)


def test_workspace_employee_is_not_a_b2b_user():
    user = WorkspaceUser(
        {"id": 18, "company_id": 3, "full_name": "Xodim", "role": "performer"}
    )

    # The id is still the employee's, for everything that wants an employee.
    assert _get_user_id(_request(user)) == 18
    assert _get_b2b_user_id(_request(user)) is None


def test_b2b_account_keeps_its_id():
    user = SimpleNamespace(id=9, company_id=3, role="owner", is_authenticated=True)

    assert _get_b2b_user_id(_request(user)) == 9


def test_dict_shaped_b2b_user_keeps_its_id():
    """Some auth paths hand the view a plain dict rather than a wrapper."""
    assert _get_b2b_user_id(_request({"id": 4, "company_id": 3})) == 4


def test_no_user_at_all():
    assert _get_b2b_user_id(_request(None)) is None
