from django.urls import path

from apps.b2b.integrations.ai_views import (
    AiConnectionView,
    AiConversationListView,
    AiConversationView,
    AiImportView,
    AiMessageView,
    AiProjectListView,
)
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
    # Claude and ChatGPT share one set of views; the provider is in the path.
    # `<provider>` is checked against `IntegrationProvider.AI` in the view —
    # a path converter would also do it, but a 404 with the list of accepted
    # names in it is a friendlier answer than a route that does not exist.
    path("<slug:provider>/", AiConnectionView.as_view(), name="ws-integration-ai"),
    path("<slug:provider>/import/", AiImportView.as_view(), name="ws-integration-ai-import"),
    path(
        "<slug:provider>/projects/",
        AiProjectListView.as_view(),
        name="ws-integration-ai-projects",
    ),
    path(
        "<slug:provider>/conversations/",
        AiConversationListView.as_view(),
        name="ws-integration-ai-conversations",
    ),
    path(
        "<slug:provider>/conversations/<int:conversation_id>/",
        AiConversationView.as_view(),
        name="ws-integration-ai-conversation",
    ),
    path(
        "<slug:provider>/conversations/<int:conversation_id>/messages/",
        AiMessageView.as_view(),
        name="ws-integration-ai-messages",
    ),
]
