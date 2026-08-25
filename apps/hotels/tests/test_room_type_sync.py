"""The room-type import, against a supplier that paginates badly.

Two behaviours of the live Hotelios API drive the shape of this code, and both
cost real data before they were handled:

  * A multi-page result repeats rows — across pages and *within* a page. A
    straight walk of `GetHotelRoomTypeList` read 32,727 rows to find 7,658
    distinct ones, and silently missed others.
  * `room_type_id` is documented as globally unique and is not. Id 20000281
    comes back under six different hotels, so keying rows on it alone
    overwrites one hotel's rooms with another's.

The fake client below reproduces both. What is pinned is the outcome: every
room type of every requested hotel, exactly once, attributed to the right
hotel.
"""
from __future__ import annotations

from unittest.mock import patch

from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.hotels import sync

PAGE_SIZE = 10


class FakeClient:
    """Answers like the real API: one page holds `PAGE_SIZE` rows, and a result
    that does not fit is padded with duplicates instead of being sliced.
    """

    def __init__(self, catalogue: dict[int, list[int]]):
        #: hotel_id -> the room_type_ids that hotel has
        self.catalogue = catalogue
        self.calls: list[tuple[int, tuple[int, ...]]] = []

    def get_room_type_page(self, *, page: int = 1, hotel_ids=None):
        hotel_ids = list(hotel_ids or self.catalogue)
        self.calls.append((page, tuple(hotel_ids)))

        rows = [
            {"id": room_type_id, "hotel_id": hotel_id}
            for hotel_id in hotel_ids
            for room_type_id in self.catalogue.get(hotel_id, [])
        ]

        if len(rows) <= PAGE_SIZE:
            return rows, {"page": 1, "total_pages": 1}

        # Too big for a page: report several pages and hand back a page's worth
        # of rows chosen badly enough to repeat — exactly what staging does.
        total_pages = -(-len(rows) // PAGE_SIZE)
        start = ((page - 1) * PAGE_SIZE // 2) % max(len(rows) - PAGE_SIZE, 1)
        return rows[start:start + PAGE_SIZE], {"page": page, "total_pages": total_pages}


def _run_sync(catalogue: dict[int, list[int]], batch_size: int = 4):
    client = FakeClient(catalogue)
    hotels = [{"id": hotel_id} for hotel_id in catalogue]
    written: list[tuple[int, int]] = []

    with (
        patch.object(sync, "ROOM_TYPE_BATCH_SIZE", batch_size),
        patch.object(sync.repo, "fetch_hotels", return_value=(hotels, len(hotels))),
        patch.object(sync.repo, "start_sync_run", return_value={"id": 1}),
        patch.object(sync.repo, "update_sync_run"),
        patch.object(sync.repo, "finish_sync_run"),
        patch.object(
            sync.repo,
            "upsert_room_type",
            side_effect=lambda payload: written.append(
                (payload["hotel_id"], payload["id"])
            ),
        ),
    ):
        result = sync.sync_room_types(client)

    return result, written, client


class TestCompleteness:
    def test_a_small_catalogue_is_imported_once_each(self):
        catalogue = {1: [11, 12], 2: [21], 3: [31, 32, 33]}
        result, written, _ = _run_sync(catalogue)

        assert sorted(written) == [(1, 11), (1, 12), (2, 21), (3, 31), (3, 32), (3, 33)]
        assert result["records"] == 6

    def test_nothing_is_written_twice_when_the_provider_repeats_rows(self):
        # Enough rooms that batches overflow a page and the fake starts
        # duplicating, which is where the old code inflated its counts.
        catalogue = {hotel: [hotel * 100 + n for n in range(6)] for hotel in range(1, 9)}
        _, written, _ = _run_sync(catalogue)

        assert len(written) == len(set(written))
        assert len(written) == 48

    def test_every_hotel_is_still_covered_when_pages_overflow(self):
        catalogue = {hotel: [hotel * 100 + n for n in range(6)] for hotel in range(1, 9)}
        _, written, _ = _run_sync(catalogue)

        assert {hotel for hotel, _ in written} == set(catalogue)
        for hotel, room_types in catalogue.items():
            assert {rt for h, rt in written if h == hotel} == set(room_types)

    def test_an_overflowing_batch_is_split_rather_than_paged(self):
        catalogue = {hotel: [hotel * 100 + n for n in range(6)] for hotel in range(1, 9)}
        _, _, client = _run_sync(catalogue, batch_size=8)

        # A split asks about a smaller set of hotels; paging asks page 2 of the
        # same one. Splitting is the trustworthy move, so it must be the one
        # that happened.
        assert any(len(hotels) < 8 for _, hotels in client.calls)
        assert all(page == 1 for page, _ in client.calls)


class TestSharedRoomTypeIds:
    """The same `room_type_id` under two hotels must produce two rows."""

    def test_a_shared_id_is_kept_for_both_hotels(self):
        catalogue = {1: [20000281], 2: [20000281], 3: [20000281]}
        _, written, _ = _run_sync(catalogue)

        assert sorted(written) == [(1, 20000281), (2, 20000281), (3, 20000281)]

    def test_a_shared_id_inside_one_batch_is_not_collapsed(self):
        # Both hotels land in the same request, so the response carries the id
        # twice — once per hotel. Deduplicating on the id alone would drop one.
        catalogue = {1: [20000281, 11], 2: [20000281, 21]}
        _, written, _ = _run_sync(catalogue, batch_size=8)

        assert sorted(written) == [(1, 11), (1, 20000281), (2, 21), (2, 20000281)]


class TestForeignRows:
    """A paged response can carry rows for hotels that were not asked about."""

    def test_rows_outside_the_requested_batch_are_discarded(self):
        class LeakyClient(FakeClient):
            def get_room_type_page(self, *, page=1, hotel_ids=None):
                rows, pagination = super().get_room_type_page(
                    page=page, hotel_ids=hotel_ids
                )
                rows = rows + [{"id": 999, "hotel_id": 4242}]
                return rows, pagination

        catalogue = {1: [11], 2: [21]}
        client = LeakyClient(catalogue)
        hotels = [{"id": h} for h in catalogue]
        written: list[tuple[int, int]] = []

        with (
            patch.object(sync.repo, "fetch_hotels", return_value=(hotels, len(hotels))),
            patch.object(sync.repo, "start_sync_run", return_value={"id": 1}),
            patch.object(sync.repo, "update_sync_run"),
            patch.object(sync.repo, "finish_sync_run"),
            patch.object(
                sync.repo,
                "upsert_room_type",
                side_effect=lambda p: written.append((p["hotel_id"], p["id"])),
            ),
        ):
            sync.sync_room_types(client)

        assert (4242, 999) not in written
        assert sorted(written) == [(1, 11), (2, 21)]


class TestRunRecording:
    def test_a_failing_import_is_recorded_as_failed(self):
        class BrokenClient:
            def get_room_type_page(self, **kwargs):
                raise RuntimeError("upstream is down")

        with (
            patch.object(sync.repo, "fetch_hotels", return_value=([{"id": 1}], 1)),
            patch.object(sync.repo, "start_sync_run", return_value={"id": 7}),
            patch.object(sync.repo, "update_sync_run"),
            patch.object(sync.repo, "finish_sync_run") as finished,
            patch.object(sync.repo, "upsert_room_type"),
        ):
            try:
                sync.sync_room_types(BrokenClient())
            except RuntimeError:
                pass

        # The run row is what makes a broken nightly import visible without
        # reading the worker log, so it must not be left saying "running".
        finished.assert_called_once()
        assert finished.call_args.kwargs["status"] == "failed"
        assert "upstream is down" in finished.call_args.kwargs["error"]
