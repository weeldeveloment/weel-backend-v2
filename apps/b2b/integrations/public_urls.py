"""The routes Meta itself calls. Mounted at `/api/b2b/integrations/`.

Kept out of `urls.py` so the difference is structural rather than remembered:
everything in that file requires the owner's workspace login, and everything
in this one is reachable by anybody — which is why one proves itself with a
one-time `state` and the other with an HMAC.
"""
from django.urls import path

from apps.b2b.integrations.public_views import (
    MetaOAuthCallbackView,
    MetaWebhookView,
)

urlpatterns = [
    path("meta/callback/", MetaOAuthCallbackView.as_view(), name="b2b-meta-callback"),
    path("meta/webhook/", MetaWebhookView.as_view(), name="b2b-meta-webhook"),
]
