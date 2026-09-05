"""The thread list's `kind` — pinned here after the assistant row went."""
from __future__ import annotations

from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.b2b.workspace.views import _thread_payload


def test_a_thread_payload_names_its_kind():
    saved = {"id": 3, "kind": "saved", "participant_ids": [], "unread": 0}
    assert _thread_payload(saved)["kind"] == "saved"
    # A row read by a query that did not select the column is an ordinary
    # chat, which is what every row was before the column existed.
    assert _thread_payload({"id": 4})["kind"] == "chat"
