from django.apps import AppConfig


class RecommendationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.recommendation"
    label = "recommendation"

    def ready(self):
        try:
            import recommendation.signals  # noqa: F401
        except ImportError:
            pass
