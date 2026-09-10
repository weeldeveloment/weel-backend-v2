"""The one search box for people: name, handle or phone.

Shared by the roster (`/team/`) and the "So'rov yuborish" picker
(`/org/people/`). Both used to match the typed text against each column as
it stood, which is why a phone typed with spaces, or a name typed in the
other order, found nobody. Both queries read the roster row as `e` and its
account as `a`.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

#: Something typed as a phone number: digits with the spacing, "+" and
#: brackets people write one with — "+998 90 111-22-33", "(90) 1112233".
_PHONE_QUERY = re.compile(r"^[\d\s+()\-]+$")

#: The apostrophes an Uzbek name is spelled with. "O'ktam", "O‘ktam" and
#: "Oʻktam" are one name typed on three keyboards, so both sides of the
#: comparison drop them — and "Oktam" finds it too.
_APOSTROPHES = "'‘’ʻʼ`"

_BARE_PHONE = "regexp_replace({}, '[^0-9]', '', 'g') LIKE %s"
_BARE_NAME = "regexp_replace({{}}, '[{}]', '', 'g') ILIKE %s".format(
    "".join("''" if ch == "'" else ch for ch in _APOSTROPHES)
)


def _digits_needle(digits: str) -> str:
    """The part of a typed number worth matching on. A full number is cut
    to its last nine digits — the subscriber number — so "+998901112233"
    finds a row stored as "901112233" and the other way round."""
    return f"%{digits[-9:] if len(digits) > 9 else digits}%"


def people_search_clause(
    search: str, *, extra_columns: Sequence[str] = ()
) -> tuple[str, list[Any]]:
    """The `AND …` to append to a people query, and its parameters.

    Phones are compared digits-only on both sides. A roster stores
    "+998901112233" while people type "90 111 22 33", and a text match
    between the two finds nothing.

    Everything else is split into words, and every word has to match
    somewhere. A roster writes "Yusupov Aziz" while the account says
    "Aziz Yusupov"; matching the whole string meant only one of the two
    orders ever found them.

    `extra_columns` are matched like the position — plain text, per word —
    for a query that has more to offer, such as the roster's department.
    """
    text = search.strip()
    digits = re.sub(r"\D", "", text)
    if digits and _PHONE_QUERY.match(text):
        needle = _digits_needle(digits)
        return (
            " AND (" + _BARE_PHONE.format("e.phone")
            + " OR " + _BARE_PHONE.format("a.phone") + ")",
            [needle, needle],
        )

    clauses: list[str] = []
    params: list[Any] = []
    for word in text.split():
        # A leading "@" is how a handle is written, not part of the stored one.
        word = word.lstrip("@")
        name_word = word.translate({ord(ch): None for ch in _APOSTROPHES})
        if not name_word:
            continue
        needle = f"%{name_word}%"
        parts = [
            _BARE_NAME.format("e.full_name"),
            _BARE_NAME.format("a.first_name"),
            _BARE_NAME.format("a.last_name"),
            "e.position ILIKE %s",
            *(f"{column} ILIKE %s" for column in extra_columns),
            "COALESCE(a.username, e.username) ILIKE %s",
        ]
        word_params: list[Any] = [needle] * len(parts)
        word_digits = re.sub(r"\D", "", word)
        if word_digits and word_digits == re.sub(r"[+()\-]", "", word):
            parts += [_BARE_PHONE.format("e.phone"), _BARE_PHONE.format("a.phone")]
            word_params += [_digits_needle(word_digits)] * 2
        clauses.append("(" + " OR ".join(parts) + ")")
        params += word_params
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params
