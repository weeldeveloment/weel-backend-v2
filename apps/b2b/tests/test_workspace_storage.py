"""The 5 GB per-company storage allowance.

Every byte the workspace stores is a row in ``b2b_workspace_file``, so the
quota is a SUM over that table and the enforcement is one gate in front of
every upload path. These cover the gate and the arithmetic; the SQL is mocked,
because the thing that can silently be wrong is the boundary — off by one on
"exactly full", or a check that happens after the bytes are already on disk.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.b2b.workspace import storage

GB = 1024**3


def _used(value: int):
    return patch.object(storage, "used_bytes", return_value=value)


class TestQuotaGate:
    def test_an_upload_that_fits_passes(self):
        with _used(1 * GB):
            storage.assert_can_store(company_id=1, size=100)

    def test_filling_the_quota_exactly_is_allowed(self):
        # The last byte that fits has to fit. A `>=` here would refuse an
        # upload that lands exactly on the limit.
        with _used(5 * GB - 100):
            storage.assert_can_store(company_id=1, size=100)

    def test_one_byte_over_is_refused(self):
        with _used(5 * GB - 100), pytest.raises(storage.StorageQuotaExceeded):
            storage.assert_can_store(company_id=1, size=101)

    def test_a_full_company_cannot_upload_anything(self):
        with _used(5 * GB), pytest.raises(storage.StorageQuotaExceeded):
            storage.assert_can_store(company_id=1, size=1)

    def test_the_refusal_says_how_much_is_left(self):
        # Under the per-upload cap, so this reaches the quota check rather
        # than being turned away on its own size first.
        incoming = 150 * 1024**2
        with _used(5 * GB - 100 * 1024**2):
            with pytest.raises(storage.StorageQuotaExceeded) as exc:
                storage.assert_can_store(company_id=1, size=incoming)
        assert exc.value.available == 100 * 1024**2
        assert exc.value.quota == 5 * GB
        assert exc.value.incoming == incoming

    def test_size_is_checked_before_the_quota(self):
        # An empty company uploading 2 GB is refused for being one huge file,
        # not for being out of room — the messages say different things and
        # the client shows different screens.
        with _used(0), pytest.raises(storage.UploadTooLarge):
            storage.assert_can_store(company_id=1, size=2 * GB)

    def test_an_oversized_file_is_refused_even_on_an_empty_account(self):
        # Checked before the quota: a single huge upload has to be rejected on
        # its own size, or it gets streamed to disk in full before anything
        # looks at it.
        with _used(0), pytest.raises(storage.UploadTooLarge) as exc:
            storage.assert_can_store(company_id=1, size=500 * 1024**2)
        assert exc.value.limit == storage.max_upload_bytes()


class TestUsage:
    def test_usage_reports_the_remainder_and_percentage(self):
        with _used(1 * GB), patch.object(storage, "usage_by_kind", return_value={}):
            usage = storage.usage(company_id=1)
        assert usage["used_bytes"] == 1 * GB
        assert usage["quota_bytes"] == 5 * GB
        assert usage["available_bytes"] == 4 * GB
        assert usage["used_percent"] == 20.0

    def test_available_never_goes_negative(self):
        # A quota lowered after the fact, or rows written around the gate.
        # The app draws a bar from this; a negative would invert it.
        with _used(6 * GB), patch.object(storage, "usage_by_kind", return_value={}):
            usage = storage.usage(company_id=1)
        assert usage["available_bytes"] == 0

    def test_every_kind_is_present_even_when_empty(self):
        # The client should not need to know the full list of kinds to render
        # an empty breakdown row.
        with patch.object(storage, "fetch_all", return_value=[
            {"kind": "file", "bytes": 500, "files": 2},
        ]):
            breakdown = storage.usage_by_kind(company_id=1)
        assert breakdown["file"] == {"bytes": 500, "files": 2}
        assert breakdown["chat"] == {"bytes": 0, "files": 0}
        assert breakdown["voucher"] == {"bytes": 0, "files": 0}

    def test_a_company_with_nothing_stored_reads_zero(self):
        with patch.object(storage, "fetch_one", return_value={"used": None}):
            assert storage.used_bytes(company_id=1) == 0
