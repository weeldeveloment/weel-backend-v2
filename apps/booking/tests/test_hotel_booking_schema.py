from datetime import date
from unittest.mock import patch

from apps.hotels.repository import (
    calculate_stay_price,
    find_client_hotel_booking_across_schemas,
    find_hotel_booking_across_schemas,
    list_client_hotel_bookings_across_schemas,
)


def _run_in_schema(schema_name, query):
    return query()


def test_client_booking_detail_searches_tenant_schemas():
    """The booking is found whichever schema holds it, in one query.

    This used to walk the schemas one at a time and stop at the first hit; it
    is now a single UNION. The guarantee callers rely on — the right schema
    comes back alongside the row — is what is asserted here.
    """
    with (
        patch(
            "apps.property.hotel_repository.list_hotel_organizations",
            return_value=[{"schema_name": "tenant_one"}, {"schema_name": "tenant_two"}],
        ),
        patch(
            "apps.hotels.repository._pms_room_columns_by_schema",
            return_value={"tenant_one": set(), "tenant_two": set()},
        ),
        patch(
            "apps.hotels.repository.fetch_all",
            return_value=[{"_schema_rank": 1, "id": 4, "tenant_schema": "tenant_two"}],
        ) as fetch_all,
    ):
        resolved = find_client_hotel_booking_across_schemas(4, 267)

    assert resolved == ("tenant_two", {"id": 4, "tenant_schema": "tenant_two"})
    assert fetch_all.call_count == 1
    sql = fetch_all.call_args.args[0]
    assert '"tenant_one".pms_booking' in sql
    assert '"tenant_two".pms_booking' in sql


def test_client_booking_detail_prefers_the_first_schema_holding_the_id():
    """A booking id is only unique *within* a schema.

    Two tenants can each own a row with id 4, and the old loop resolved that
    by taking whichever came first in the roster. The union has to break the
    tie the same way, or a guest could be shown someone else's booking — so
    the query orders by the schema's position and takes one row.
    """
    with (
        patch(
            "apps.property.hotel_repository.list_hotel_organizations",
            return_value=[{"schema_name": "tenant_one"}, {"schema_name": "tenant_two"}],
        ),
        patch(
            "apps.hotels.repository._pms_room_columns_by_schema",
            return_value={"tenant_one": set(), "tenant_two": set()},
        ),
        patch("apps.hotels.repository.fetch_all", return_value=[]) as fetch_all,
    ):
        find_client_hotel_booking_across_schemas(4, 267)

    sql = fetch_all.call_args.args[0]
    assert "ORDER BY matches._schema_rank" in sql
    assert "LIMIT 1" in sql
    # Rank is the roster position, passed ahead of each branch's own parameters.
    ranks = [p for p in fetch_all.call_args.args[1] if isinstance(p, int) and p in (0, 1)]
    assert ranks[:1] == [0]


def test_client_booking_detail_falls_back_to_visiting_each_schema():
    with (
        patch(
            "apps.property.hotel_repository.list_hotel_organizations",
            return_value=[{"schema_name": "tenant_one"}, {"schema_name": "tenant_two"}],
        ),
        patch(
            "apps.hotels.repository._pms_room_columns_by_schema",
            return_value={"tenant_one": set(), "tenant_two": set()},
        ),
        patch("apps.hotels.repository.fetch_all", side_effect=RuntimeError("boom")),
        patch(
            "apps.property.hotel_repository._run_in_schema",
            side_effect=_run_in_schema,
        ) as run_in_schema,
        patch(
            "apps.hotels.repository.get_client_hotel_booking",
            side_effect=[None, {"id": 4}],
        ),
    ):
        resolved = find_client_hotel_booking_across_schemas(4, 267)

    assert resolved == ("tenant_two", {"id": 4, "tenant_schema": "tenant_two"})
    assert [call.args[0] for call in run_in_schema.call_args_list] == [
        "tenant_one",
        "tenant_two",
    ]


def test_client_booking_list_merges_tenant_schemas():
    """One statement across the schemas, newest booking first.

    The list used to be assembled by visiting each schema in turn; it is now a
    single UNION that tags every row with the schema it came from. What the
    caller depends on — every schema represented, newest first — is unchanged,
    so that is what this asserts.
    """
    with (
        patch(
            "apps.property.hotel_repository.list_hotel_organizations",
            return_value=[{"schema_name": "tenant_one"}, {"schema_name": "tenant_two"}],
        ),
        patch(
            "apps.hotels.repository._pms_room_columns_by_schema",
            return_value={"tenant_one": set(), "tenant_two": set()},
        ),
        patch(
            "apps.hotels.repository.fetch_all",
            return_value=[
                {"id": 1, "created_at": 1, "tenant_schema": "tenant_one"},
                {"id": 2, "created_at": 2, "tenant_schema": "tenant_two"},
            ],
        ) as fetch_all,
    ):
        bookings = list_client_hotel_bookings_across_schemas(267)

    assert [(row["id"], row["tenant_schema"]) for row in bookings] == [
        (2, "tenant_two"),
        (1, "tenant_one"),
    ]
    # One query, not one per schema — the point of the change.
    assert fetch_all.call_count == 1
    sql = fetch_all.call_args.args[0]
    assert '"tenant_one".pms_booking' in sql
    assert '"tenant_two".pms_booking' in sql


def test_client_booking_list_falls_back_to_visiting_each_schema():
    """A schema the union cannot read must not take the whole list down."""
    with (
        patch(
            "apps.property.hotel_repository.list_hotel_organizations",
            return_value=[{"schema_name": "tenant_one"}, {"schema_name": "tenant_two"}],
        ),
        patch(
            "apps.hotels.repository._pms_room_columns_by_schema",
            return_value={"tenant_one": set(), "tenant_two": set()},
        ),
        patch("apps.hotels.repository.fetch_all", side_effect=RuntimeError("boom")),
        patch(
            "apps.property.hotel_repository._run_in_schema",
            side_effect=_run_in_schema,
        ),
        patch(
            "apps.hotels.repository.list_client_hotel_bookings",
            side_effect=[[{"id": 1, "created_at": 1}], [{"id": 2, "created_at": 2}]],
        ),
    ):
        bookings = list_client_hotel_bookings_across_schemas(267)

    assert [(row["id"], row["tenant_schema"]) for row in bookings] == [
        (2, "tenant_two"),
        (1, "tenant_one"),
    ]


def test_hotel_booking_task_uses_explicit_tenant_schema():
    with (
        patch("apps.property.hotel_repository.list_hotel_organizations") as list_orgs,
        patch(
            "apps.property.hotel_repository._run_in_schema",
            side_effect=_run_in_schema,
        ) as run_in_schema,
        patch(
            "apps.hotels.repository.get_hotel_booking_by_id",
            return_value={"id": 4},
        ),
    ):
        resolved = find_hotel_booking_across_schemas(4, schema_name="tenant_two")

    assert resolved == ("tenant_two", {"id": 4, "tenant_schema": "tenant_two"})
    list_orgs.assert_not_called()
    assert run_in_schema.call_args.args[0] == "tenant_two"


def test_stay_price_rejects_room_without_price():
    with patch(
        "apps.hotels.repository.fetch_one",
        return_value={"base_price": None, "currency": "USD"},
    ):
        result = calculate_stay_price(
            2,
            check_in=date(2026, 7, 17),
            check_out=date(2026, 7, 21),
        )

    assert result is None
