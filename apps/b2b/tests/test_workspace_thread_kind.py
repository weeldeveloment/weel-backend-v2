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


def test_a_thread_payload_says_what_its_last_message_carried():
    """A room whose latest message is a voice note has no `text` to print on
    the list; the attachment's name, type and length are what the app names
    it by — the same fields a message's own `attachment` has."""
    row = {
        "id": 5, "last_message_id": 77, "last_message_sender_id": 3,
        "last_message_text": "", "last_message_created_at": None,
        "last_message_attachment_name": "voice_1.m4a",
        "last_message_attachment_type": "audio/mp4",
        "last_message_attachment_duration_ms": 2100,
    }
    assert _thread_payload(row)["last_message"]["attachment"] == {
        "name": "voice_1.m4a", "content_type": "audio/mp4", "duration_ms": 2100,
    }
    # Words alone: no attachment, rather than one with three empty fields.
    row.update(last_message_attachment_name=None, last_message_text="salom")
    assert _thread_payload(row)["last_message"]["attachment"] is None
