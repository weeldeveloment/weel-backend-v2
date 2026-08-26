from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PropertyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "property"
    verbose_name = _("Property")
