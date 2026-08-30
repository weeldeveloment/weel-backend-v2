from django.urls import path

from apps.b2b.integrations.views import (
    IntegrationListView,
    MetaAppView,
    MetaConnectView,
    MetaDisconnectView,
    MetaPageView,
    MetaSyncView,
)

#: Mounted under `/api/b2b/workspace/integrations/`. Everything here needs the
#: workspace login *and* the owner/administrator role — see `permissions`.
urlpatterns = [
    path("", IntegrationListView.as_view(), name="ws-integrations"),
    path("meta/connect/", MetaConnectView.as_view(), name="ws-integration-meta-connect"),
    # This workspace's own Facebook app, for the companies that cannot use the
    # deployment's — see `MetaAppView`.
    path("meta/app/", MetaAppView.as_view(), name="ws-integration-meta-app"),
    path("meta/", MetaDisconnectView.as_view(), name="ws-integration-meta"),
    path("meta/sync/", MetaSyncView.as_view(), name="ws-integration-meta-sync"),
    path(
        "meta/pages/<int:page_row_id>/",
        MetaPageView.as_view(),
        name="ws-integration-meta-page",
    ),
]
