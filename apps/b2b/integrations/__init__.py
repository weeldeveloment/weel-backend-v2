"""Outside services plugged into a workspace's funnel.

One provider so far — Meta's lead ads. A person fills in a Facebook or
Instagram form, Meta posts a `leadgen` webhook here, and the enquiry lands on
the sales board as an ordinary lead marked `source = meta`.

The pieces, in the order a connection goes through them:

* `views.MetaConnectView`  — the owner taps "Ulash"; we hand back an
  authorise URL and open it in the phone's browser.
* `public_views.MetaOAuthCallbackView` — Meta sends the browser back here.
  The code is exchanged for a long-lived token, every page the user granted
  is stored and subscribed to `leadgen`, and the browser is shown a page
  telling them to go back to the app.
* `public_views.MetaWebhookView` — where the leads actually arrive.
* `ingest` — one Meta lead → one row on the board.

Nothing here is reachable by a plain employee: connecting an outside service
commits the whole company's funnel to it, so it is the owner's or an
administrator's ("lider") call. See `permissions.CanManageIntegrations`.
"""
