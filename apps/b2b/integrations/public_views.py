"""The two halves of the Meta flow that carry no workspace login.

Neither of these is called by our app:

* [MetaOAuthCallbackView] is where Meta sends the *browser* after somebody
  authorises us. It has no Authorization header — it is a redirect — so the
  one-time `state` issued by `MetaConnectView` is what says which workspace
  this is. It renders a page rather than JSON, because a human is looking at
  it, and its whole message is "go back to the app".

* [MetaWebhookView] is where the leads arrive. Also unauthenticated, for the
  same reason every webhook is: Meta has no credential of ours to present. It
  proves itself with an HMAC over the body, signed with the app secret — see
  `meta.verify_signature`. An unsigned delivery is dropped, not processed:
  this endpoint's whole job is to put rows on somebody's sales board, and
  anyone who learned the URL could otherwise fill it with anything.
"""
from __future__ import annotations

import hmac
import html
import json
import logging

from django.core.cache import cache
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.b2b.integrations import credentials, crypto, meta
from apps.b2b.integrations import repository as int_repo
from apps.b2b.integrations.tasks import ingest_meta_lead
from apps.b2b.integrations.views import state_key
from apps.b2b.models import IntegrationProvider, IntegrationStatus

logger = logging.getLogger(__name__)


# ─── The page the browser lands on ────────────────────────────────────────────

def _result_page(title: str, body: str, ok: bool = True, note: str = "") -> HttpResponse:
    """A self-contained page. No stylesheet, no script, no link out.

    This is opened in whatever browser the phone uses and read for about two
    seconds. Everything it needs is inline so it renders identically wherever
    it lands, and the one thing it says is what to do next.

    Every piece of text is escaped. Some of it is not ours — `error` comes
    straight off the query string, page names and error messages come from
    Meta — and this page is served from our own API domain.
    """
    title = html.escape(title)
    body = html.escape(body)
    if note:
        body += "<br><br>" + html.escape(note)
    colour = "#15BE63" if ok else "#E5484D"
    document = f"""<!doctype html>
<html lang="uz"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
</head>
<body style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
             background:#F6F7F9;display:flex;align-items:center;justify-content:center;
             min-height:100vh;padding:24px;">
  <div style="max-width:420px;background:#fff;border-radius:20px;padding:32px 26px;
              text-align:center;box-shadow:0 8px 30px rgba(16,24,40,.08);">
    <div style="width:64px;height:64px;border-radius:20px;margin:0 auto 18px;
                background:{colour}1A;color:{colour};font-size:32px;line-height:64px;">
      {'✓' if ok else '!'}
    </div>
    <h1 style="margin:0 0 10px;font-size:20px;color:#101828;">{title}</h1>
    <p style="margin:0;font-size:15px;line-height:1.5;color:#667085;">{body}</p>
  </div>
</body></html>"""
    return HttpResponse(document, content_type="text/html; charset=utf-8")


class MetaOAuthCallbackView(APIView):
    """GET /api/b2b/integrations/meta/callback/ — Meta sends the browser here."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        error = request.query_params.get("error_description") or request.query_params.get("error")
        if error:
            return _result_page("Ulanmadi", f"Meta ruxsat bermadi: {error}", ok=False)

        code = (request.query_params.get("code") or "").strip()
        state = (request.query_params.get("state") or "").strip()
        if not code or not state:
            return _result_page("Ulanmadi", "So‘rov to‘liq emas.", ok=False)

        issued = cache.get(state_key(state))
        # `delete` answers whether it removed anything, which makes the state
        # single-use even when the same callback URL is opened twice at once
        # — only one of the two gets to attach pages to the company.
        if not issued or not cache.delete(state_key(state)):
            # Expired, or never issued. Both are refused the same way, so
            # nothing about which it was leaks to whoever is holding the URL.
            return _result_page(
                "Muddati tugadi",
                "Ulanish so‘rovi eskirdi. Ilovaga qaytib, qaytadan urinib ko‘ring.",
                ok=False,
            )

        # The company comes from the state our own server issued to a signed-in
        # owner, never from anything in this URL. That is the whole reason the
        # Facebook account signing in here can only ever be attached to the
        # workspace that pressed "Ulash".
        company_id = issued["company_id"]
        employee_id = issued["employee_id"]
        creds = credentials.global_credentials()

        try:
            short = meta.exchange_code(code, creds)
            long_lived = meta.long_lived_token(short["access_token"], creds)
            account = meta.me(long_lived["access_token"])
            pages = meta.list_pages(long_lived["access_token"])
        except Exception as exc:  # noqa: BLE001 — a human is reading the answer
            logger.exception("Meta OAuth failed for company %s", company_id)
            return _result_page("Ulanmadi", str(exc), ok=False)

        account_id = str(account.get("id") or "") or None
        existing = int_repo.get_integration(company_id, IntegrationProvider.META)
        live = bool(existing and existing.get("access_token_enc"))
        # Anybody on the roster may connect (see `permissions`), so a company
        # already connected will see a second Facebook account sign in — an
        # employee who runs another of its pages, say. That login *adds* its
        # pages to the connection. It does not take the connection over, and
        # it does not drop the pages the first account brought: only that same
        # account, re-picking its pages on Meta's screen, rewrites the list.
        adding = live and existing.get("account_id") != account_id

        # Sorted before anything is written, so a login that brings nothing
        # usable can leave a working connection exactly as it found it.
        usable, taken = [], []
        for page in pages:
            page_id = str(page.get("id") or "")
            if not page_id or not page.get("access_token"):
                continue
            # Somebody who administers pages for several companies grants us
            # all of them in one login. A page another workspace already has
            # stays with that workspace — its leads are its customers.
            if int_repo.page_held_elsewhere(page_id, company_id):
                taken.append(page.get("name") or page_id)
                continue
            usable.append(page)

        no_pages = (
            "Bu Facebook hisobida siz boshqaradigan sahifa yo‘q. Sahifa "
            "administratori bo‘lgan hisob bilan kiring."
        )
        if not usable and live:
            # Nothing to add, and a connection that works: touch nothing. The
            # pages the company already has keep arriving.
            return _result_page(
                "Sahifa ulanmadi", _taken_note(taken) or no_pages, ok=False,
            )

        if adding:
            integration = existing
        else:
            integration = int_repo.upsert_integration(
                company_id=company_id,
                provider=IntegrationProvider.META,
                account_id=account_id,
                account_name=(account.get("name") or "") or None,
                access_token_enc=crypto.encrypt(long_lived["access_token"]),
                token_expires_at=long_lived.get("expires_at"),
                scopes=",".join(meta.SCOPES),
                connected_by_id=employee_id,
            )
            if not integration:
                return _result_page("Ulanmadi", "Ulanish saqlanmadi.", ok=False)

        stored, failed, kept = 0, [], []
        for page in usable:
            page_id = str(page["id"])
            name = page.get("name") or page_id
            subscribed = True
            try:
                meta.subscribe_page(page_id, page["access_token"])
            except meta.MetaError as exc:
                # Worth storing anyway. A page we could not subscribe is one
                # the catch-up sync can still read, and a half-connected
                # account the owner can see is more useful than a page that
                # vanished from the list with no explanation.
                subscribed = False
                failed.append(f"{name}: {exc}")
            row = int_repo.upsert_page(
                integration_id=integration["id"],
                company_id=company_id,
                page_id=page_id,
                page_name=(page.get("name") or "")[:300],
                access_token_enc=crypto.encrypt(page["access_token"]),
                subscribed=subscribed,
            )
            if row:
                stored += 1
                kept.append(page_id)
            else:
                # Lost the race to another company connecting it this second.
                taken.append(name)

        taken_note = _taken_note(taken)

        if not adding:
            # What the owner ticked on Meta's screen this time is the whole
            # list. A page from an earlier login that is not in it any more
            # leaves.
            int_repo.delete_pages_except(integration["id"], kept)

        if not stored:
            if not adding:
                int_repo.set_integration_status(
                    integration["id"], IntegrationStatus.ERROR,
                    error=(taken_note or no_pages)[:1000],
                )
            return _result_page(
                "Sahifa ulanmadi", taken_note or no_pages, ok=False,
            )

        # The connection's own status is its account's. An added page that
        # failed to subscribe says so on its own row and on the page below,
        # not by turning the whole connection red.
        if adding:
            pass
        elif failed:
            int_repo.set_integration_status(
                integration["id"], IntegrationStatus.ERROR,
                error="; ".join([*failed, taken_note] if taken_note else failed)[:1000],
            )
        elif taken_note:
            # Connected, and working for the pages that are ours — the note is
            # there so the owner is not left wondering where the others went.
            int_repo.set_integration_status(
                integration["id"], IntegrationStatus.CONNECTED,
                error=taken_note[:1000],
            )

        # The leads that were submitted before this moment. A company connects
        # Meta because it already runs ads, and an integration whose first
        # lead arrives tomorrow reads as broken today.
        try:
            from apps.b2b.integrations.tasks import sync_meta_pages

            sync_meta_pages.delay(company_id)
        except Exception:  # noqa: BLE001
            logger.exception("Could not queue the first Meta sync for %s", company_id)

        notes = [taken_note] if taken_note else []
        if adding and failed:
            notes.append("Obuna bo‘lmadi: " + "; ".join(failed))
        return _result_page(
            "Meta ulandi",
            (f"{stored} ta sahifa qo‘shildi." if adding
             else f"{stored} ta sahifa ulandi.")
            + " Ilovaga qayting — yangi leadlar savdo varonkasida paydo bo‘ladi.",
            note=" ".join(notes),
        )


def _taken_note(taken: list[str]) -> str:
    if not taken:
        return ""
    return (
        "Bu sahifalar boshqa Weel kompaniyasiga ulangan, shu sababli "
        "qo‘shilmadi: " + ", ".join(taken) + ". Ularni avval o‘sha "
        "kompaniyada uzing."
    )


# ─── Where the leads arrive ───────────────────────────────────────────────────

@method_decorator(csrf_exempt, name="dispatch")
class MetaWebhookView(APIView):
    """GET  /api/b2b/integrations/meta/webhook/ — Meta's subscription check.
    POST /api/b2b/integrations/meta/webhook/ — a form was submitted."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        """The handshake Meta performs once, when the webhook is configured
        in Weel's app. It quotes `META_WEBHOOK_VERIFY_TOKEN` and expects its
        challenge echoed back as plain text."""
        mode = request.query_params.get("hub.mode")
        token = (request.query_params.get("hub.verify_token") or "").strip()
        challenge = request.query_params.get("hub.challenge") or ""
        expected = credentials.global_credentials().verify_token
        if (
            mode == "subscribe" and token and expected
            and hmac.compare_digest(token, expected)
        ):
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse("forbidden", status=403, content_type="text/plain")

    @swagger_auto_schema(auto_schema=None)
    def post(self, request):
        """One delivery, answered fast.

        Meta wants a 200 within seconds and retries anything else, so nothing
        slow happens on this path: the delivery is logged, the work is queued,
        and the answer goes back. A 200 for a payload we could not use is
        correct — Meta redelivering it would not make it usable.

        The signature is checked first, over the raw body, with Weel's app
        secret — every delivery for every company comes from that one app.
        Which company a lead belongs to is then decided by the *page* it
        names, and only by that: `b2b_integration_page.page_id` is unique, so
        a page answers with exactly one company or none.
        """
        body = request.body or b""
        signature = request.headers.get("X-Hub-Signature-256")
        secret = credentials.global_credentials().app_secret
        if not meta.verify_signature(body, signature, secret):
            # Loud rather than a quiet 200: a signature that never verifies is
            # either an attempt at this endpoint or an app secret rotated
            # behind our back, and both should show up.
            logger.warning("Meta webhook with a bad signature was dropped.")
            return Response({"detail": "invalid signature"},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            payload = json.loads(body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            return Response({"status": "ignored"})

        if payload.get("object") != "page":
            return Response({"status": "ignored"})

        queued = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "leadgen":
                    continue
                queued += self._queue(change.get("value") or {})
        return Response({"status": "ok", "queued": queued})

    def _queue(self, value: dict) -> int:
        """One `leadgen` change. 1 if queued, 0 if not."""
        leadgen_id = str(value.get("leadgen_id") or "").strip()
        page_id = str(value.get("page_id") or "").strip()
        if not leadgen_id or not page_id:
            return 0

        page = int_repo.find_page(page_id)
        if not page:
            # A page no workspace has connected, or one that was disconnected.
            # Not an error and not worth retrying.
            logger.info("Meta lead for unknown page %s", page_id)
            return 0

        if not page.get("is_active"):
            return 0

        event = int_repo.claim_event(
            provider=IntegrationProvider.META,
            external_id=leadgen_id,
            company_id=page["company_id"],
            page_id=page_id,
            payload=value,
        )
        if event is None:
            return 0  # A redelivery of something already in hand.

        try:
            ingest_meta_lead.delay(
                page["id"], leadgen_id,
                str(value.get("form_id") or ""), event["id"],
            )
        except Exception:  # noqa: BLE001
            # The queue is down. Give the delivery back so Meta's own retry
            # can have another go at it rather than losing the customer.
            logger.exception("Could not queue Meta lead %s", leadgen_id)
            int_repo.release_event(event["id"])
            return 0
        return 1
