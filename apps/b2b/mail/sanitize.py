"""Makes an inbound HTML mail body safe to render.

Every message here was written by a stranger and is displayed inside an
authenticated session on both the dashboard and the phone, so the body is
hostile input in the most literal sense. It is cleaned **once, on the way in**,
and the stored result is what clients render — no client is trusted to sanitise
correctly, and there are two of them written in different languages.

What is removed and why:

* ``<script>``, ``<iframe>``, ``<object>``, ``<embed>``, event handlers — the
  obvious script-injection routes.
* ``<style>`` blocks and ``position:``/``z-index`` in inline styles — CSS alone
  is enough to overlay a fake login prompt on the surrounding page.
* ``javascript:`` and ``data:`` URLs on links.
* Remote images, unless the reader asks for them. A unique image URL in a mail
  body is the standard read-receipt tracker; loading it silently tells the
  sender when, and from which IP, the message was opened.

The plain-text part is kept alongside and is what the phone falls back to.
"""
from __future__ import annotations

import re

# Structure, emphasis, links, tables, images. Deliberately no <form>, <input>,
# <button>: a rendered form inside a mail body is a credential-phishing surface
# and no legitimate newsletter needs one to display.
ALLOWED_TAGS = [
    "p", "br", "div", "span", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "b", "strong", "i", "em", "u", "s", "strike", "sub", "sup", "small",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "pre", "code",
    "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
]

ALLOWED_ATTRIBUTES = {
    "*": ["style", "class", "title", "dir", "lang", "align"],
    "a": ["href", "target", "rel"],
    "img": ["src", "alt", "width", "height"],
    "td": ["colspan", "rowspan", "valign", "bgcolor"],
    "th": ["colspan", "rowspan", "valign", "bgcolor"],
    "table": ["width", "cellpadding", "cellspacing", "border", "bgcolor"],
    "col": ["width", "span"],
}

ALLOWED_PROTOCOLS = ["http", "https", "mailto", "cid"]

# Layout properties are excluded on purpose: see the module docstring on
# overlay attacks. Colour, spacing and type are what mail actually needs.
#
# Shorthand `background` is left out while `background-color` is allowed: the
# shorthand accepts `url(...)`, which would smuggle a tracking pixel back in
# through CSS after we deferred it in the markup.
_ALLOWED_CSS = {
    "color", "background-color",
    "font-size", "font-family", "font-weight", "font-style",
    "text-align", "text-decoration", "line-height", "letter-spacing",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "border", "border-top", "border-bottom", "border-left", "border-right",
    "border-color", "border-width", "border-style", "border-radius",
    "width", "max-width", "height", "vertical-align", "white-space",
}

def _attribute_filter(tag: str, name: str, value: str) -> bool:
    """Per-attribute gate, called by bleach for every surviving attribute.

    It can only allow or deny — rewriting a value is not possible here, which
    is why ``style`` contents are handled by bleach's own CSS sanitiser and
    the remote-image rewrite happens in a separate pass afterwards.
    """
    allowed = set(ALLOWED_ATTRIBUTES.get("*", [])) | set(ALLOWED_ATTRIBUTES.get(tag, []))
    return name in allowed


# bleach only strips a disallowed *tag*; the text between <script> and
# </script> survives as body text, which would render the payload to the
# reader as a wall of source. These have to go before bleach sees them.
_RAW_TEXT_BLOCK_RE = re.compile(
    r"(?is)<\s*(script|style|title|noscript|template)\b[^>]*>.*?<\s*/\s*\1\s*>"
)
# ...including the unclosed forms, which browsers happily consume to EOF.
_RAW_TEXT_OPEN_RE = re.compile(r"(?is)<\s*(script|style|noscript|template)\b[^>]*>.*\Z")


def _css_sanitizer():
    from bleach.css_sanitizer import CSSSanitizer

    return CSSSanitizer(allowed_css_properties=sorted(_ALLOWED_CSS))


def sanitize_html(html: str, *, block_remote_images: bool = True) -> str:
    """Clean one mail body. Returns "" for empty or unusable input."""
    if not html or not html.strip():
        return ""

    import bleach

    stripped = _RAW_TEXT_BLOCK_RE.sub(" ", html)
    stripped = _RAW_TEXT_OPEN_RE.sub(" ", stripped)

    cleaned = bleach.clean(
        stripped,
        tags=set(ALLOWED_TAGS),
        attributes=_attribute_filter,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=_css_sanitizer(),
        # Drop disallowed tags rather than escaping them into visible text.
        strip=True,
        strip_comments=True,
    )

    if block_remote_images:
        cleaned = _defer_remote_images(cleaned)

    # Mail links open outside the app; `noopener` stops the opened page from
    # reaching back through `window.opener`, and `noreferrer` keeps the
    # dashboard URL out of the destination's logs.
    cleaned = re.sub(
        r"<a\s",
        '<a target="_blank" rel="noopener noreferrer nofollow" ',
        cleaned,
        flags=re.I,
    )
    return cleaned


def _defer_remote_images(html: str) -> str:
    """Move remote image URLs out of ``src`` and into ``data-blocked-src``.

    Inline images the message carried with it (``cid:``) are left alone — they
    resolve against our own attachment store and leak nothing. The client shows
    a "show images" affordance and swaps the attribute back if the reader asks.
    """
    def replace(match: re.Match) -> str:
        tag = match.group(0)
        src = match.group(1)
        if src.lower().startswith("cid:"):
            return tag
        return tag.replace(f'src="{src}"', f'data-blocked-src="{src}" src=""', 1) \
                  .replace(f"src='{src}'", f"data-blocked-src='{src}' src=''", 1)

    return re.sub(r"<img[^>]*?src=[\"']([^\"']*)[\"'][^>]*>", replace, html, flags=re.I)


def make_snippet(text: str, limit: int = 200) -> str:
    """The one-line preview the thread list shows."""
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def html_to_text(html: str) -> str:
    """Crude fallback for messages that arrived with no text/plain part."""
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)

    import html as html_module

    text = html_module.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
