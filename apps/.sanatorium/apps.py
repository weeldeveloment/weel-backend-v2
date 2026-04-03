from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SanatoriumConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = ".sanatorium"
    verbose_name = _("Sanatorium")
