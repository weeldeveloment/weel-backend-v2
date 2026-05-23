from django.urls import include, path

urlpatterns = [
    path("user/", include(("users.urls", "users"), "users")),
    path("property/", include(("property.urls", "property"), "property")),
    path("story/", include(("stories.urls", "stories"), "stories")),
    path("booking/", include(("booking.urls", "booking"), "booking")),
    path("notification/", include(("notification.urls", "notification"), "notification")),
    path("bot/", include(("bot.urls", "bot"), "bot")),
    path("logs/", include(("shared.urls", "shared"), "shared")),
    path("chat/", include(("chat.urls", "chat"), "chat")),
    path("admin-auth/", include(("apps.admin_auth.urls", "admin_auth"), "admin_auth")),
    path("property/", include(("recommendation.urls", "recommendation"), "recommendation")),
]
