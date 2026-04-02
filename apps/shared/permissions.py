import os
from dotenv import load_dotenv

from rest_framework.permissions import BasePermission, SAFE_METHODS

from django.utils.translation import gettext_lazy as _

from init_data_py import InitData

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN_APP")


def _user_role(user) -> str | None:
    role = getattr(user, "role", None)
    if role:
        return str(role)

    # Fallback for legacy ORM user instances.
    name = user.__class__.__name__.lower()
    if name == "partner":
        return "partner"
    if name == "client":
        return "client"
    return None


class IsPartner(BasePermission):
    message = _("Authentication credentials were not provided for partner.")

    def has_permission(self, request, view):
        return bool(request.user and _user_role(request.user) == "partner" and getattr(request.user, "is_active", False))


class IsClient(BasePermission):
    message = _("Authentication credentials were not provided for client.")

    def has_permission(self, request, view):
        return bool(request.user and _user_role(request.user) == "client" and getattr(request.user, "is_active", False))


class IsClientOrPartner(BasePermission):
    """Allow access for either authenticated Client or Partner."""

    message = _("Authentication credentials were not provided.")

    def has_permission(self, request, view):
        if not request.user:
            return False
        role = _user_role(request.user)
        if role == "client" and getattr(request.user, "is_active", False):
            return True
        if role == "partner" and getattr(request.user, "is_active", False):
            return True
        return False


class IsPartnerOwnerProperty(BasePermission):
    """Allow partners to edit or delete only their own properties"""

    message = _("You don't have permission to modify")

    def has_permission(self, request, view):
        # User must be an active partner
        if not (request.user and _user_role(request.user) == "partner" and getattr(request.user, "is_active", False)):
            self.message = _("Authentication credentials were not provided for partner")
            return False
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True

        owner = getattr(obj, "partner", None)
        if owner is None and hasattr(obj, "property"):
            owner = getattr(obj.property, "partner", None)

        owner_id = getattr(owner, "id", owner)
        request_user_id = getattr(request.user, "id", request.user)
        if owner_id != request_user_id:
            if request.method in ["PUT", "PATCH"]:
                self.message = _("You don't have permission to edit this property")
            elif request.method == "DELETE":
                self.message = _("You don't have permission to delete this property")
            return False

        return True


class IsTelegramWebApp(BasePermission):
    message = _("This endpoint is available only via Telegram Web App")

    def has_permission(self, request, view):
        raw_init_data = request.headers.get("X-Telegram-InitData")
        if not raw_init_data:
            return False
        try:
            init_data = InitData.parse(raw_init_data)
            if not init_data.validate(BOT_TOKEN, lifetime=3600):
                print(init_data)
                print(init_data.user)
                return False
            request.telegram_user = init_data.user
            return True
        except Exception:
            return False
