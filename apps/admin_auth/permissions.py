from rest_framework.permissions import BasePermission


class IsAdminUser(BasePermission):
    message = "Access denied. Admin privileges required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user:
            return False
        return bool(getattr(user, "role", None) == "admin" and getattr(user, "is_active", False))
