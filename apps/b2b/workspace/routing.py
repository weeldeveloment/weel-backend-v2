from django.urls import re_path

from apps.b2b.workspace import consumers

# Two paths for the same consumer, matching how `chat.routing` does it: some
# deployments terminate the app under `/api/`, and a client that guessed wrong
# would otherwise get a 404 handshake with nothing to explain it.
websocket_urlpatterns = [
    re_path(r"ws/b2b/workspace/chat/$", consumers.WorkspaceChatConsumer.as_asgi()),
    re_path(r"api/ws/b2b/workspace/chat/$", consumers.WorkspaceChatConsumer.as_asgi()),
]
