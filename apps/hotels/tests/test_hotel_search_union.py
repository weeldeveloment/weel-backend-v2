"""The cross-schema hotel search must return exactly what the per-schema
search did.

Search had no test of its own when it was rewritten from "a query per tenant
schema" into a single ``UNION ALL``, so this is the safety net that rewrite
needed: for a matrix of filter combinations, the union path and the legacy
per-schema path are run against the same database and their results compared
row for row. The legacy path is kept in the module as the fallback for a
schema that cannot be queried, so this is testing live code, not a museum
piece.

Needs a database with at least one hotel organization; skipped otherwise, so
it stays useful locally and harmless in a bare CI container.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from apps.hotels import repository as repo

pytestmark = pytest.mark.django_db


def _organizations():
    from apps.property.hotel_repository import _safe_schema_name, list_hotel_organizations

    try:
        return [
            org for org in list_hotel_organizations()
            if _safe_schema_name(org.get("schema_name"))
        ]
    except Exception:
        return []


def _identity(rows):
    """What a caller actually depends on: which hotels came back, in which
    order, at what price. Comparing whole rows would fail on nothing more than
    a timestamp's microseconds."""
    return [
        (
            row.get("tenant_schema"),
            row.get("id"),
            row.get("min_price"),
            row.get("currency"),
            row.get("available_rooms"),
            row.get("booking_count"),
        )
        for row in rows
    ]


CHECK_IN = date.today() + timedelta(days=7)
CHECK_OUT = CHECK_IN + timedelta(days=2)

FILTER_CASES = [
    pytest.param({}, id="no-filters"),
    pytest.param({"city": "Toshkent"}, id="city"),
    pytest.param({"guests": 3}, id="guests"),
    pytest.param({"star_rating": 4}, id="stars"),
    pytest.param({"is_recommended": True}, id="recommended"),
    pytest.param({"check_in": CHECK_IN, "check_out": CHECK_OUT}, id="date-range"),
    pytest.param(
        {"check_in": CHECK_IN, "check_out": CHECK_OUT, "guests": 2},
        id="date-range-and-guests",
    ),
    pytest.param({"min_capacity": 2, "max_capacity": 4}, id="capacity-range"),
    pytest.param({"room_type_presets": ["standard"]}, id="room-type-preset"),
    pytest.param({"meal_plans": ["breakfast"]}, id="meal-plan"),
    # The rate-plan filter is the one whose SQL shape depends on how far each
    # schema has been migrated — the reason `_rate_plan_tables_by_schema`
    # exists at all.
    pytest.param({"rate_plans": ["BAR"]}, id="rate-plan"),
]


@pytest.mark.parametrize("filters", FILTER_CASES)
def test_union_matches_per_schema(filters):
    organizations = _organizations()
    if not organizations:
        pytest.skip("no hotel organizations in this database")

    union_rows = repo._collect_hotel_rows(**filters)
    legacy_rows = repo._collect_hotel_rows_per_schema(
        organizations,
        city=filters.get("city"),
        check_in=filters.get("check_in"),
        check_out=filters.get("check_out"),
        guests=filters.get("guests", 1),
        star_rating=filters.get("star_rating"),
        weel_classification=filters.get("weel_classification"),
        is_recommended=filters.get("is_recommended"),
        themes=filters.get("themes"),
        price_min=filters.get("price_min"),
        price_max=filters.get("price_max"),
        budget_max=filters.get("budget_max"),
        room_types=filters.get("room_types"),
        room_type_presets=filters.get("room_type_presets"),
        rate_plans=filters.get("rate_plans"),
        meal_plans=filters.get("meal_plans"),
        min_capacity=filters.get("min_capacity"),
        max_capacity=filters.get("max_capacity"),
        allow_multi_room=filters.get("allow_multi_room", False),
        lat=filters.get("lat"),
        lon=filters.get("lon"),
        radius_km=filters.get("radius_km", 10.0),
    )

    # Order across schemas is not promised by either path — sorting is applied
    # afterwards by `search_hotels` — so compare as sets of identities.
    assert sorted(_identity(union_rows)) == sorted(_identity(legacy_rows))


def test_union_runs_one_query_per_search(django_assert_num_queries):
    """The point of the rewrite: cost stops growing with the tenant roster.

    Two queries — the organization roster, then the union — no matter how many
    schemas there are.
    """
    organizations = _organizations()
    if not organizations:
        pytest.skip("no hotel organizations in this database")

    with django_assert_num_queries(2):
        repo._collect_hotel_rows()


def test_search_page_counts_without_searching_twice(django_assert_num_queries):
    """`search_hotels` + `count_hotels` used to run the whole search twice.

    `search_hotels_page` answers both from one pass.
    """
    if not _organizations():
        pytest.skip("no hotel organizations in this database")

    with django_assert_num_queries(2):
        rows, total = repo.search_hotels_page(limit=20)

    assert isinstance(total, int)
    assert len(rows) <= 20
    assert total >= len(rows)


def test_qualify_tenant_tables_leaves_longer_identifiers_alone():
    sql = "SELECT * FROM pms_room r JOIN pms_room_type_rate_plan rt ON rt.room_type_id = r.room_type_id"
    qualified = repo._qualify_tenant_tables(sql, "tenant_x")

    assert '"tenant_x".pms_room r' in qualified
    assert '"tenant_x".pms_room_type_rate_plan rt' in qualified
    # Column names that merely start with a table name must not be rewritten.
    assert "rt.room_type_id = r.room_type_id" in qualified
    assert '"tenant_x".pms_room_type_id' not in qualified


def test_qualify_tenant_tables_is_idempotent():
    once = repo._qualify_tenant_tables("SELECT * FROM pms_property p", "tenant_x")
    assert repo._qualify_tenant_tables(once, "tenant_x") == once
