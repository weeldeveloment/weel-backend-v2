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

def _result_page(title: str, body: str, ok: bool = True) -> HttpResponse:
    """A self-contained page. No stylesheet, no script, no link out.

    This is opened in whatever browser the phone uses and read for about two
    seconds. Everything it needs is inline so it renders identically wherever
    it lands, and the one thing it says is what to do next.
    """
    colour = "#15BE63" if ok else "#E5484D"
    html = f"""<!doctype html>
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
    return HttpResponse(html, content_type="text/html; charset=utf-8")


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
        if not issued:
            # Expired, or never issued. Both are refused the same way, so
            # nothing about which it was leaks to whoever is holding the URL.
            return _result_page(
                "Muddati tugadi",
                "Ulanish so‘rovi eskirdi. Ilovaga qaytib, qaytadan urinib ko‘ring.",
                ok=False,
            )
        cache.delete(state_key(state))

        company_id = issued["company_id"]
        employee_id = issued["employee_id"]

        # Whichever app this workspace signed in through — theirs if they set
        # one up, ours otherwise. The token has to be exchanged against the
        # same app that issued the code; using the other one fails with an
        # error about a redirect URI that looks nothing like the real cause.
        creds = credentials.for_company(company_id)

        try:
            short = meta.exchange_code(code, creds)
            long_lived = meta.long_lived_token(short["access_token"], creds)
            account = meta.me(long_lived["access_token"])
            pages = meta.list_pages(long_lived["access_token"])
        except Exception as exc:  # noqa: BLE001 — a human is reading the answer
            logger.exception("Meta OAuth failed for company %s", company_id)
            return _result_page("Ulanmadi", str(exc), ok=False)

        integration = int_repo.upsert_integration(
            company_id=company_id,
            provider=IntegrationProvider.META,
            account_id=str(account.get("id") or "") or None,
            account_name=(account.get("name") or "") or None,
            access_token_enc=crypto.encrypt(long_lived["access_token"]),
            token_expires_at=long_lived.get("expires_at"),
            scopes=",".join(meta.SCOPES),
            connected_by_id=employee_id,
        )
        if not integration:
            return _result_page("Ulanmadi", "Ulanish saqlanmadi.", ok=False)

        stored, failed = 0, []
        for page in pages:
            page_id = str(page.get("id") or "")
            page_token = page.get("access_token")
            if not page_id or not page_token:
                continue
            subscribed = True
            try:
                meta.subscribe_page(page_id, page_token)
            except meta.MetaError as exc:
                # Worth storing anyway. A page we could not subscribe is one
                # the catch-up sync can still read, and a half-connected
                # account the owner can see is more useful than a page that
                # vanished from the list with no explanation.
                subscribed = False
                failed.append(f"{page.get('name') or page_id}: {exc}")
            row = int_repo.upsert_page(
                integration_id=integration["id"],
                company_id=company_id,
                page_id=page_id,
                page_name=(page.get("name") or "")[:300],
                access_token_enc=crypto.encrypt(page_token),
                subscribed=subscribed,
            )
            if row:
                stored += 1

        if not stored:
            int_repo.set_integration_status(
                integration["id"],
                IntegrationStatus.ERROR,
                error="Bu hisobda boshqariladigan sahifa topilmadi.",
            )
            return _result_page(
                "Sahifa topilmadi",
                "Bu Facebook hisobida siz boshqaradigan sahifa yo‘q. Sahifa "
                "administratori bo‘lgan hisob bilan kiring.",
                ok=False,
            )

        if failed:
            int_repo.set_integration_status(
                integration["id"], IntegrationStatus.ERROR,
                error="; ".join(failed)[:1000],
            )

        # The leads that were submitted before this moment. A company connects
        # Meta because it already runs ads, and an integration whose first
        # lead arrives tomorrow reads as broken today.
        try:
            from apps.b2b.integrations.tasks import sync_meta_pages

            sync_meta_pages.delay(company_id)
        except Exception:  # noqa: BLE001
            logger.exception("Could not queue the first Meta sync for %s", company_id)

        return _result_page(
            "Meta ulandi",
            f"{stored} ta sahifa ulandi. Ilovaga qayting — yangi leadlar "
            f"savdo varonkasida paydo bo‘ladi.",
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
        """The handshake Meta performs once, when the webhook is configured.

        It sends a challenge and expects it echoed back as plain text, having
        first quoted a token only the two of us know.

        One URL now serves several apps — ours, and every workspace connecting
        through its own — and the handshake carries no company. So the token
        itself is the identity: it matches the deployment's, or it matches one
        workspace's stored token, or it is refused. That is safe because a
        verify token *is* a shared secret; a caller who knows one already
        knows the thing this check exists to prove.
        """
        mode = request.query_params.get("hub.mode")
        token = (request.query_params.get("hub.verify_token") or "").strip()
        challenge = request.query_params.get("hub.challenge") or ""
        if mode != "subscribe" or not token:
            return HttpResponse("forbidden", status=403, content_type="text/plain")

        expected = credentials.global_credentials().verify_token
        known = bool(expected) and hmac.compare_digest(token, expected)
        if not known:
            known = int_repo.find_by_verify_token(token) is not None

        if known:
            return HttpResponse(challenge, content_type="text/plain")
        return HttpResponse("forbidden", status=403, content_type="text/plain")

    @swagger_auto_schema(auto_schema=None)
    def post(self, request):
        """One delivery, answered fast.

        Meta wants a 200 within seconds and retries anything else, so nothing
        slow happens on this path: the delivery is logged, the work is queued,
        and the answer goes back. A 200 for a payload we could not use is
        correct — Meta redelivering it would not make it usable.

        **The signature is checked per page, not once for the request.** One
        URL receives deliveries from several apps and each signs with its own
        secret, so which secret to check against is decided by the page the
        delivery names. Parsing the body before verifying is safe — parsing is
        not acting — and nothing is queued until the delivery has proved it
        was signed by the app that owns that page.
        """
        body = request.body or b""
        signature = request.headers.get("X-Hub-Signature-256")

        try:
            payload = json.loads(body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            return Response({"status": "ignored"})

        if payload.get("object") != "page":
            return Response({"status": "ignored"})

        queued, refused = 0, 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "leadgen":
                    continue
                result = self._queue(change.get("value") or {}, body, signature)
                if result < 0:
                    refused += 1
                else:
                    queued += result

        if refused and not queued:
            # Nothing in this delivery proved itself. Answering 403 rather
            # than 200 is deliberate: a signature that never verifies is
            # either an attempt at this endpoint or an app secret rotated
            # behind our back, and both should be loud.
            logger.warning("Meta webhook with a bad signature was dropped.")
            return Response({"detail": "invalid signature"},
                            status=status.HTTP_403_FORBIDDEN)
        return Response({"status": "ok", "queued": queued})

    def _queue(self, value: dict, body: bytes, signature: str | None) -> int:
        """One `leadgen` change. 1 if queued, 0 if ignored, -1 if the
        signature did not check out."""
        leadgen_id = str(value.get("leadgen_id") or "").strip()
        page_id = str(value.get("page_id") or "").strip()
        if not leadgen_id or not page_id:
            return 0

        page = int_repo.find_page(page_id)
        if not page:
            # Somebody else's page, or one this workspace disconnected. Not an
            # error and not worth retrying — Meta keeps sending until the
            # subscription is removed on their side.
            logger.info("Meta lead for unknown page %s", page_id)
            return 0

        # Whose app signs for this page. Resolved from the page rather than
        # from the request, because the request cannot say — every app posts
        # to the same URL.
        creds = credentials.for_company(page["company_id"])
        if not meta.verify_signature(body, signature, creds.app_secret):
            logger.warning(
                "Meta webhook for page %s was not signed by its app.", page_id
            )
            return -1

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
