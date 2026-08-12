"""Parsing and threading of incoming mail.

Everything here operates on raw RFC 822 bytes, because that is what actually
arrives — real mail is full of encoded headers, mixed charsets, HTML-only
bodies and reply prefixes in three languages, and each of those has broken a
naive parser at some point.
"""
from email.message import EmailMessage

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.b2b.mail.imap_sync import _addresses, _bodies, _decode, _reference_ids
from apps.b2b.mail.repository import normalize_subject
from apps.b2b.mail.sanitize import html_to_text, make_snippet, sanitize_html
from apps.b2b.mail.serializers import MailSendSerializer


def _raw(message: EmailMessage):
    import email

    return email.message_from_bytes(message.as_bytes())


# ─── Headers ──────────────────────────────────────────────────────────────────

def test_decodes_an_rfc2047_encoded_subject():
    # What Gmail sends for a non-ASCII subject.
    assert _decode("=?utf-8?B?0J/RgNC40LLQtdGC?=") == "Привет"


def test_a_malformed_header_falls_back_to_the_raw_value():
    # Spam routinely carries broken encodings; losing the header entirely is
    # worse for the reader than showing it as-is.
    assert _decode("=?nonsense?Q?abc?=") == "=?nonsense?Q?abc?="


def test_decodes_uzbek_latin_subject():
    message = EmailMessage()
    message["Subject"] = "To'lov hisob-fakturasi №42"
    assert _decode(_raw(message).get("Subject")) == "To'lov hisob-fakturasi №42"


def test_extracts_named_and_bare_addresses():
    message = EmailMessage()
    message["To"] = "Aziz Karimov <aziz@gmail.com>, plain@example.com"
    assert _addresses(_raw(message), "To") == [
        ("Aziz Karimov", "aziz@gmail.com"),
        ("", "plain@example.com"),
    ]


def test_ignores_a_header_value_that_is_not_an_address():
    message = EmailMessage()
    message["To"] = "undisclosed-recipients:;"
    assert _addresses(_raw(message), "To") == []


def test_references_are_newest_first_and_deduplicated():
    message = EmailMessage()
    message["References"] = "<a@x> <b@x> <c@x>"
    message["In-Reply-To"] = "<c@x>"
    # The direct parent is the likeliest thread match, so it must be tried
    # before older ancestors.
    assert _reference_ids(_raw(message)) == ["<c@x>", "<b@x>", "<a@x>"]


# ─── Bodies ───────────────────────────────────────────────────────────────────

def test_splits_a_multipart_alternative_into_text_and_html():
    message = EmailMessage()
    message.set_content("Salom")
    message.add_alternative("<p>Salom</p>", subtype="html")

    text, html, attachments = _bodies(_raw(message))
    assert text == "Salom"
    assert "<p>Salom</p>" in html
    assert attachments == []


def test_collects_attachments_with_their_filename_and_bytes():
    message = EmailMessage()
    message.set_content("Hisobot ilovada")
    message.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf",
                           filename="hisobot.pdf")

    _, _, attachments = _bodies(_raw(message))
    assert len(attachments) == 1
    assert attachments[0]["filename"] == "hisobot.pdf"
    assert attachments[0]["payload"] == b"%PDF-1.4 fake"
    assert attachments[0]["is_inline"] is False


def test_a_windows1251_body_is_decoded_not_mangled():
    message = EmailMessage()
    message.set_content("Привет мир", charset="cp1251")
    text, _, _ = _bodies(_raw(message))
    assert text == "Привет мир"


def test_an_unknown_charset_falls_back_to_utf8_instead_of_raising():
    import email

    raw = (
        b"Content-Type: text/plain; charset=x-not-a-charset\r\n"
        b"\r\n"
        b"hello\r\n"
    )
    text, _, _ = _bodies(email.message_from_bytes(raw))
    assert text.strip() == "hello"


# ─── Threading ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("subject", [
    "Hisobot",
    "Re: Hisobot",
    "RE: RE: Hisobot",
    "Fwd: Hisobot",
    "  re:  Hisobot  ",
    "Отв: Hisobot",
    "Javob: Hisobot",
])
def test_reply_prefixes_collapse_onto_one_thread_key(subject):
    assert normalize_subject(subject) == "hisobot"


def test_different_subjects_do_not_share_a_thread_key():
    assert normalize_subject("Hisobot") != normalize_subject("Shartnoma")


def test_an_empty_subject_is_handled():
    assert normalize_subject(None) == ""
    assert normalize_subject("   ") == ""


# ─── Sanitisation ─────────────────────────────────────────────────────────────

def test_strips_script_tags_and_their_contents():
    cleaned = sanitize_html("<p>Salom</p><script>alert(document.cookie)</script>")
    assert "script" not in cleaned.lower()
    # The payload must not survive as visible text either.
    assert "alert" not in cleaned
    assert "Salom" in cleaned


@pytest.mark.parametrize("payload", [
    '<a href="javascript:alert(1)">click</a>',
    '<img src="x" onerror="alert(1)">',
    '<iframe src="https://evil.example"></iframe>',
    '<div onclick="steal()">text</div>',
    '<object data="evil.swf"></object>',
    '<svg onload="alert(1)"></svg>',
])
def test_known_xss_vectors_do_not_survive(payload):
    cleaned = sanitize_html(payload).lower()
    assert "javascript:" not in cleaned
    assert "onerror" not in cleaned
    assert "onclick" not in cleaned
    assert "onload" not in cleaned
    assert "<iframe" not in cleaned
    assert "<object" not in cleaned


def test_position_absolute_is_dropped_from_inline_styles():
    # CSS alone is enough to overlay a fake login prompt on the app around it.
    cleaned = sanitize_html('<div style="position:absolute; top:0; color:red">x</div>')
    assert "position" not in cleaned
    assert "top" not in cleaned
    assert "color:red" in cleaned.replace(" ", "")


def test_css_url_is_dropped_but_ordinary_styling_survives():
    cleaned = sanitize_html('<p style="background: url(https://tracker.example/x.png); font-size:14px">x</p>')
    assert "url(" not in cleaned
    assert "tracker.example" not in cleaned
    assert "font-size:14px" in cleaned.replace(" ", "")


def test_remote_images_are_deferred_rather_than_loaded():
    # A unique image URL in a mail body is the standard read-receipt tracker.
    cleaned = sanitize_html('<img src="https://tracker.example/pixel.png">')
    assert "data-blocked-src" in cleaned
    assert 'src=""' in cleaned


def test_inline_cid_images_are_left_alone():
    cleaned = sanitize_html('<img src="cid:logo123">')
    assert "cid:logo123" in cleaned
    assert "data-blocked-src" not in cleaned


def test_links_are_given_noopener_and_open_outside_the_app():
    cleaned = sanitize_html('<a href="https://example.com">x</a>')
    assert 'rel="noopener noreferrer nofollow"' in cleaned
    assert 'target="_blank"' in cleaned


def test_tables_and_formatting_survive_because_real_mail_uses_them():
    cleaned = sanitize_html(
        '<table><tr><td bgcolor="#eee"><b>Summa</b></td><td>1 000 000</td></tr></table>'
    )
    assert "<table>" in cleaned
    assert "<td" in cleaned
    assert "<b>" in cleaned


def test_html_to_text_produces_a_readable_fallback():
    text = html_to_text("<p>Birinchi</p><p>Ikkinchi</p><script>x=1</script>")
    assert "Birinchi" in text
    assert "Ikkinchi" in text
    assert "x=1" not in text


def test_snippet_is_truncated_on_a_character_budget():
    assert make_snippet("a" * 500, limit=50).endswith("…")
    assert len(make_snippet("a" * 500, limit=50)) == 50
    assert make_snippet("qisqa") == "qisqa"


def test_snippet_collapses_whitespace():
    assert make_snippet("bir\n\n  ikki\t uch") == "bir ikki uch"


# ─── Send validation ──────────────────────────────────────────────────────────

def test_rejects_a_message_with_no_recipient():
    serializer = MailSendSerializer(data={"to": [], "body_text": "salom"})
    assert not serializer.is_valid()
    assert "to" in serializer.errors


def test_rejects_an_invalid_address():
    serializer = MailSendSerializer(data={"to": ["not-an-email"], "body_text": "salom"})
    assert not serializer.is_valid()


def test_rejects_an_empty_body():
    serializer = MailSendSerializer(data={"to": ["a@gmail.com"]})
    assert not serializer.is_valid()


def test_normalises_and_deduplicates_recipients():
    serializer = MailSendSerializer(data={
        "to": ["  Aziz@Gmail.COM ", "aziz@gmail.com"],
        "body_text": "salom",
    })
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["to"] == ["aziz@gmail.com"]


def test_rejects_a_message_addressed_to_a_crowd(settings_override=None):
    # A personal mailbox blasting hundreds of addresses reads as spam to the
    # receiving side and costs the sending IP its reputation.
    serializer = MailSendSerializer(data={
        "to": [f"user{i}@example.com" for i in range(200)],
        "body_text": "salom",
    })
    assert not serializer.is_valid()


def test_accepts_an_ordinary_message():
    serializer = MailSendSerializer(data={
        "to": ["mijoz@gmail.com"],
        "cc": ["boshliq@kompaniya.com"],
        "subject": "Hisob-faktura",
        "body_text": "Ilovada.",
    })
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["cc"] == ["boshliq@kompaniya.com"]
