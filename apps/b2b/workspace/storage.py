"""Per-company storage accounting for the workspace.

Every byte a company stores — a file on the shared drive, a photo sent in a
chat, a generated voucher — is one row in ``b2b_workspace_file``. The quota is
therefore ``SUM(size)`` over that table for the company, and there is no
separate counter to keep in step.

That is the whole design decision. A ``used_bytes`` column on the company would
be one write per upload and one per delete, spread across three call sites that
each have their own failure paths; the first one that forgets, or crashes
between storing the object and updating the counter, leaves a company either
unable to upload or able to store forever. Summing the rows that own the bytes
cannot drift, because there is nothing to drift from.

The cost is a SUM per upload. It is indexed on ``(company_id, kind)`` and a
company holding 5 GB of small files is in the low tens of thousands of rows, so
this is a millisecond against a request that is already writing a file.
"""
from __future__ import annotations

from django.conf import settings
from django.core.files.storage import default_storage

from apps.b2b.raw.tables import B2B_WORKSPACE_FILE_TABLE
from shared.raw.db import fetch_all, fetch_one


def photo_url(path: str | None) -> str | None:
    """A stored picture as something a client can load.

    Every column that holds a picture — ``b2b_employee.photo``,
    ``b2b_account.photo``, ``b2b_chat_thread.photo`` — stores a *path*, because
    only the server knows which backend the bytes are on and a URL written into
    a row goes stale the day that changes.

    So every payload carrying one has to resolve it, and this is the single
    place that does. It exists because they did not: uploading an avatar wrote
    the path correctly and ``/me/`` resolved it, while the roster, the chat
    rows and the join requests all shipped the bare path — so the picture
    appeared on your own profile screen and nowhere else, which reads exactly
    like the upload having failed.

    Anything already absolute is left alone: a row written before this existed,
    or an avatar that came from somewhere else entirely.
    """
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return default_storage.url(path)

# What a b2b owner gets. Overridable per deployment — a customer on a larger
# plan is a settings change, not a release.
DEFAULT_QUOTA_BYTES = 5 * 1024**3  # 5 GiB

# Uploads are also capped individually. Without it a single 5 GB request would
# be streamed to disk in full before the quota check could reject it, which is
# a denial of service with a progress bar.
DEFAULT_MAX_UPLOAD_BYTES = 200 * 1024**2  # 200 MiB


class StorageQuotaExceeded(Exception):
    """The company has no room for this upload.

    Carries the numbers so the API can tell the user how much is left rather
    than only that they are out — "2.1 GB of 5 GB free" is actionable, "quota
    exceeded" is not.
    """

    def __init__(self, *, used: int, quota: int, incoming: int):
        self.used = used
        self.quota = quota
        self.incoming = incoming
        self.available = max(0, quota - used)
        super().__init__("Storage quota exceeded.")


class UploadTooLarge(Exception):
    """One file is over the per-upload cap, regardless of what is free."""

    def __init__(self, *, size: int, limit: int):
        self.size = size
        self.limit = limit
        super().__init__("File is too large.")


def quota_bytes() -> int:
    return int(getattr(settings, "B2B_WORKSPACE_STORAGE_QUOTA_BYTES", DEFAULT_QUOTA_BYTES))


def max_upload_bytes() -> int:
    return int(getattr(settings, "B2B_WORKSPACE_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES))


def used_bytes(company_id: int) -> int:
    row = fetch_one(
        f"SELECT COALESCE(SUM(size), 0) AS used FROM {B2B_WORKSPACE_FILE_TABLE} "
        "WHERE company_id = %s",
        [company_id],
    )
    return int((row or {}).get("used") or 0)


def usage_by_kind(company_id: int) -> dict[str, dict[str, int]]:
    """Bytes and file count per kind, so the app can show where the space went.

    Kinds with nothing in them are filled in as zero rather than left out — a
    client rendering a breakdown should not have to know the full list of kinds
    to draw an empty row.
    """
    rows = fetch_all(
        f"SELECT kind, COALESCE(SUM(size), 0) AS bytes, COUNT(*) AS files "
        f"FROM {B2B_WORKSPACE_FILE_TABLE} WHERE company_id = %s GROUP BY kind",
        [company_id],
    )
    breakdown = {
        kind: {"bytes": 0, "files": 0}
        for kind in ("file", "chat", "voucher", "lead", "note")
    }
    for row in rows:
        breakdown[row["kind"]] = {
            "bytes": int(row["bytes"] or 0),
            "files": int(row["files"] or 0),
        }
    return breakdown


def usage(company_id: int) -> dict[str, object]:
    """The payload behind ``GET /storage/``."""
    used = used_bytes(company_id)
    quota = quota_bytes()
    return {
        "used_bytes": used,
        "quota_bytes": quota,
        "available_bytes": max(0, quota - used),
        # Rounded here rather than in the client, so the phone and the
        # dashboard cannot disagree about when a bar turns red.
        "used_percent": round(used / quota * 100, 1) if quota else 0.0,
        "max_upload_bytes": max_upload_bytes(),
        "by_kind": usage_by_kind(company_id),
    }


def assert_can_store(company_id: int, size: int) -> None:
    """Gate before an upload is committed.

    Raises [UploadTooLarge] or [StorageQuotaExceeded]. Call it *before*
    ``default_storage.save`` — checking afterwards means the bytes are already
    on disk and have to be deleted again, and a failure between the two leaks
    an orphan the quota will never see.
    """
    limit = max_upload_bytes()
    if size > limit:
        raise UploadTooLarge(size=size, limit=limit)

    quota = quota_bytes()
    used = used_bytes(company_id)
    if used + size > quota:
        raise StorageQuotaExceeded(used=used, quota=quota, incoming=size)
