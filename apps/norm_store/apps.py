from django.apps import AppConfig


class NormStoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "norm_store"
    verbose_name = "Norm datastore"

    def ready(self):
        from .signals import connect_norm_signals

        connect_norm_signals()
