from unittest.mock import patch

from apps.hotels.repository import (
    find_client_hotel_booking_across_schemas,
    find_hotel_booking_across_schemas,
    list_client_hotel_bookings_across_schemas,
)


def _run_in_schema(schema_name, query):
    return query()


def test_client_booking_detail_searches_tenant_schemas():
    with (
        patch(
            "apps.property.hotel_repository.list_hotel_organizations",
            return_value=[{"schema_name": "tenant_one"}, {"schema_name": "tenant_two"}],
        ),
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
    with (
        patch(
            "apps.property.hotel_repository.list_hotel_organizations",
            return_value=[{"schema_name": "tenant_one"}, {"schema_name": "tenant_two"}],
        ),
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
