"""Single-admin and env-driven access rules for the admin API."""

from __future__ import annotations

import os


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in ("1", "true", "yes", "on")


def normalized_allowed_admin_email() -> str | None:
    raw = (os.getenv("ADMIN_ALLOWED_EMAIL") or "").strip()
    return raw.lower() if raw else None


def is_email_allowed_for_admin(email: str | None) -> bool:
    allowed = normalized_allowed_admin_email()
    if not allowed:
        return True
    return (email or "").strip().lower() == allowed


def is_admin_user_creation_enabled() -> bool:
    """When ADMIN_ALLOWED_EMAIL is set, creating extra admins is off unless explicitly enabled."""
    if not normalized_allowed_admin_email():
        return True
    return _truthy_env("ADMIN_ALLOW_MULTIPLE_ADMINS")
