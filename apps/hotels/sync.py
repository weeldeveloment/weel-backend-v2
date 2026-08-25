"""Import the Hotelios static catalogue into our own tables.

Hotelios splits the world into references (countries, facilities, bed types…),
geography (regions and cities, fetched per parent) and the catalogue itself
(hotels and room types, paginated). None of it changes minute to minute, so it
is pulled on a schedule and every screen afterwards reads locally.

Each phase is separately runnable — a failed hotel import should not force the
reference lists to be re-fetched — and every run is recorded in
`hotelios_sync_run` so a stalled import is visible without reading logs.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from django.utils import timezone

from apps.hotels import raw_repository as repo
from apps.hotels.client import HoteliosClient, HoteliosError, get_client
from apps.hotels.raw.tables import (
    HOTELIOS_BED_TYPE_TABLE,
    HOTELIOS_COUNTRY_TABLE,
    HOTELIOS_EQUIPMENT_TABLE,
    HOTELIOS_FACILITY_TABLE,
    HOTELIOS_HOTEL_TYPE_TABLE,
    HOTELIOS_NEARBY_PLACE_TYPE_TABLE,
    HOTELIOS_SERVICE_IN_ROOM_TABLE,
)

logger = logging.getLogger(__name__)

# Hotelios answers `GetHotelList` 500 hotels at a time and tells us the total
# page count. This is a safety stop, not the expected end of the walk: without
# it a pagination bug on either side turns into an unbounded loop.
MAX_PAGES = 500

# How many hotels to ask for room types about at once. A page holds 1,000 room
# types and a hotel averages nine, so this stays well inside a single page
# while keeping the whole catalogue to a couple of dozen requests.
ROOM_TYPE_BATCH_SIZE = 100


def _run(scope: str, work: Callable[[dict[str, Any]], int]) -> dict[str, Any]:
    """Execute one sync phase inside a recorded run."""
    run = repo.start_sync_run(scope)
    state: dict[str, Any] = {"run_id": run["id"], "records": 0}
    try:
        records = work(state)
    except Exception as exc:
        repo.finish_sync_run(run["id"], status="failed", error=str(exc)[:2000])
        logger.exception("hotels: %s sync failed", scope)
        raise
    repo.update_sync_run(run["id"], records=records)
    repo.finish_sync_run(run["id"], status="succeeded")
    logger.info("hotels: %s sync wrote %d records.", scope, records)
    return {"scope": scope, "records": records, "run_id": run["id"]}


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def sync_references(client: HoteliosClient | None = None) -> dict[str, Any]:
    """The flat lookup lists: countries, facilities, equipment, beds, stars…"""
    client = client or get_client()

    def work(state: dict[str, Any]) -> int:
        written = 0
        written += repo.upsert_reference_rows(
            HOTELIOS_COUNTRY_TABLE, client.get_country_list()
        )
        written += repo.upsert_reference_rows(
            HOTELIOS_HOTEL_TYPE_TABLE, client.get_hotel_type_list()
        )
        written += repo.upsert_reference_rows(
            HOTELIOS_FACILITY_TABLE, client.get_facility_list(), with_filter_flag=True
        )
        written += repo.upsert_reference_rows(
            HOTELIOS_EQUIPMENT_TABLE, client.get_equipment_list(), with_filter_flag=True
        )
        written += repo.upsert_reference_rows(
            HOTELIOS_NEARBY_PLACE_TYPE_TABLE, client.get_nearby_place_type_list()
        )
        written += repo.upsert_reference_rows(
            HOTELIOS_SERVICE_IN_ROOM_TABLE, client.get_services_in_room_list()
        )
        written += repo.upsert_reference_rows(
            HOTELIOS_BED_TYPE_TABLE, client.get_bed_type_list()
        )
        written += repo.upsert_stars(client.get_star_list())
        written += repo.upsert_currencies(client.get_currency_list())
        return written

    return _run("references", work)


# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------

def sync_geography(client: HoteliosClient | None = None) -> dict[str, Any]:
    """Every region and city, in two calls.

    `GetRegionList` and `GetCityList` accept a parent id, but omitting it
    returns the complete list — all 138 regions and ~5,000 cities. Walking
    country by country instead would be several hundred requests, which the
    staging server closes the connection on partway through.
    """
    client = client or get_client()

    def work(state: dict[str, Any]) -> int:
        # Regions first: a city references one, and the foreign key is real.
        written = repo.upsert_regions(client.get_region_list())
        written += repo.upsert_cities(client.get_city_list())
        return written

    return _run("geography", work)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def sync_hotels(
    client: HoteliosClient | None = None,
    *,
    deactivate_missing: bool = True,
) -> dict[str, Any]:
    """Every hotel, page by page.

    Photos, facilities and nearby places arrive inside each hotel object, so a
    page is a complete import of the hotels on it.
    """
    client = client or get_client()

    def work(state: dict[str, Any]) -> int:
        started_at = timezone.now()
        written = 0
        page = 1
        total_pages = None

        while page <= MAX_PAGES:
            hotels, pagination = client.get_hotel_page(page=page)
            for hotel in hotels:
                repo.upsert_hotel(hotel)
                written += 1

            total_pages = pagination.get("total_pages") or total_pages or 1
            repo.update_sync_run(
                state["run_id"], pages_done=page, pages_total=total_pages, records=written
            )
            if page >= total_pages:
                break
            page += 1
        else:
            logger.warning(
                "hotels: stopped after %d pages — pagination never terminated.", MAX_PAGES
            )

        # Only safe once the whole walk finished: a partial pass would retire
        # every hotel it did not reach.
        if deactivate_missing and total_pages and page >= total_pages:
            retired = repo.deactivate_hotels_missing_since(started_at)
            if retired:
                logger.info("hotels: retired %d hotels no longer in the catalogue.", retired)

        return written

    return _run("hotels", work)


def sync_room_types(client: HoteliosClient | None = None) -> dict[str, Any]:
    """Room types, fetched a batch of hotels at a time.

    `GetHotelRoomTypeList` also accepts a bare page number, but its unfiltered
    result set is not stably ordered: rows repeat across pages *and within one
    page*, so a straight walk both re-reads thousands of rows and silently
    misses others. Asking for an explicit `hotel_ids` batch returns that
    batch's room types exactly once, which is the only form of this call that
    can be trusted to be complete.
    """
    client = client or get_client()

    def collect(batch: list[int], into: dict[tuple[int, int], dict[str, Any]]) -> None:
        """Gather every room type for these hotels, keyed by (hotel, room type).

        The key is the pair, never the room type id on its own: Hotelios
        documents that id as globally unique and its data is not — id 20000281
        is returned under six different hotels — so deduplicating on it alone
        throws away one hotel's rooms in favour of another's.

        Two supplier behaviours are handled here, and both are why this
        collects into a dict rather than writing as it reads:

          * a multi-page result repeats rows, within a page as well as across
            pages, so the same room type comes back several times;
          * a page that reports more pages behind it cannot be trusted to be a
            clean slice, so a batch that does not fit on one page is split in
            half and each half asked for separately instead.

        Splitting bottoms out at a single hotel. A hotel with more room types
        than one page holds is then paged through — deduplicated by id, which
        is the same guarantee the dict gives everywhere else.
        """
        requested = set(batch)

        def keep(room_types: list[dict[str, Any]]) -> None:
            # Only rows for the hotels actually asked about. A paged response
            # can carry rows from outside the filter, and accepting those would
            # attribute another hotel's rooms to this batch.
            for room_type in room_types:
                hotel_id, room_type_id = room_type.get("hotel_id"), room_type.get("id")
                if room_type_id is None or hotel_id not in requested:
                    continue
                into[(hotel_id, room_type_id)] = room_type

        room_types, pagination = client.get_room_type_page(page=1, hotel_ids=batch)
        total_pages = pagination.get("total_pages") or 1

        if total_pages <= 1:
            keep(room_types)
            return

        if len(batch) > 1:
            middle = len(batch) // 2
            collect(batch[:middle], into)
            collect(batch[middle:], into)
            return

        logger.info(
            "hotels: hotel %s has more room types than one page holds; paging it.",
            batch[0],
        )
        page = 1
        while page <= MAX_PAGES:
            room_types, pagination = client.get_room_type_page(page=page, hotel_ids=batch)
            keep(room_types)
            if page >= (pagination.get("total_pages") or 1):
                break
            page += 1

    def work(state: dict[str, Any]) -> int:
        hotels, _ = repo.fetch_hotels(limit=100000)
        hotel_ids = [hotel["id"] for hotel in hotels]
        batches = [
            hotel_ids[start:start + ROOM_TYPE_BATCH_SIZE]
            for start in range(0, len(hotel_ids), ROOM_TYPE_BATCH_SIZE)
        ]
        repo.update_sync_run(state["run_id"], pages_total=len(batches))

        written = 0
        for index, batch in enumerate(batches, start=1):
            found: dict[tuple[int, int], dict[str, Any]] = {}
            collect(batch, found)
            for room_type in found.values():
                repo.upsert_room_type(room_type)
                written += 1
            repo.update_sync_run(state["run_id"], pages_done=index, records=written)
        return written

    return _run("room_types", work)


def sync_hotel_services(
    client: HoteliosClient | None = None,
    *,
    hotel_ids: list[int] | None = None,
) -> dict[str, Any]:
    """In-room services, which are only available one hotel at a time.

    This is the one reference that has no bulk form, so it is kept out of the
    nightly full sync and run on demand for the hotels being displayed.
    """
    client = client or get_client()

    def work(state: dict[str, Any]) -> int:
        ids = hotel_ids
        if not ids:
            rows, _ = repo.fetch_hotels(limit=100000)
            ids = [row["id"] for row in rows]

        written = 0
        for index, hotel_id in enumerate(ids, start=1):
            try:
                services = client.get_hotel_services_in_room(hotel_id=hotel_id)
            except HoteliosError as exc:
                logger.warning("hotels: services for %s failed — %s", hotel_id, exc)
                continue
            repo.set_hotel_services_in_room(hotel_id=hotel_id, services=services)
            written += 1
            if index % 50 == 0:
                repo.update_sync_run(state["run_id"], records=written)
        return written

    return _run("hotel_services", work)


def sync_all(client: HoteliosClient | None = None) -> list[dict[str, Any]]:
    """References → geography → hotels → room types, in dependency order."""
    client = client or get_client()
    return [
        sync_references(client),
        sync_geography(client),
        sync_hotels(client),
        sync_room_types(client),
    ]
