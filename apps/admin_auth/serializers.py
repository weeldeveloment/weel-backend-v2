import logging
import os

from django.contrib.auth.hashers import check_password
from rest_framework import serializers

from .raw_repository import (
    create_admin_user,
    exists_admin_email,
    get_active_admin_by_email,
    get_admin_password_hash,
    is_super_admin,
    make_unique_admin_username,
)

logger = logging.getLogger(__name__)


class AdminLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        email = attrs.get("email")
        password = attrs.get("password")
        if not email or not password:
            raise serializers.ValidationError("Email and password are required.")

        user = get_active_admin_by_email(email)
        if not user:
            raise serializers.ValidationError("Invalid credentials.")

        # The account's own hash is authoritative. This used to compare against a single
        # shared ADMIN_LOGIN_PASSWORD and, when that env var was unset — which it was —
        # skipped the check entirely, so any password signed in any active admin.
        stored_hash = get_admin_password_hash(user.id)
        if stored_hash:
            if not check_password(password, stored_hash):
                raise serializers.ValidationError("Invalid credentials.")
            attrs["user"] = user
            return attrs

        # No hash yet: accounts predating per-admin passwords. ADMIN_LOGIN_PASSWORD keeps
        # them usable during the migration and nothing else — set a real password with
        # `manage.py set_admin_password`, then remove the variable.
        static_password = (os.getenv("ADMIN_LOGIN_PASSWORD") or "").strip()
        if not static_password:
            logger.warning(
                "Admin %s has no password set and ADMIN_LOGIN_PASSWORD is not configured; "
                "refusing the sign-in. Run: manage.py set_admin_password %s",
                email,
                email,
            )
            raise serializers.ValidationError("Invalid credentials.")
        if password != static_password:
            raise serializers.ValidationError("Invalid credentials.")
        logger.warning(
            "Admin %s signed in with the shared ADMIN_LOGIN_PASSWORD. Set a per-account "
            "password with `manage.py set_admin_password %s` and drop the variable.",
            email,
            email,
        )

        attrs["user"] = user
        return attrs


class AdminUserSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    email = serializers.EmailField(allow_null=True, required=False)
    full_name = serializers.SerializerMethodField()
    is_staff = serializers.SerializerMethodField()
    is_superuser = serializers.SerializerMethodField()

    def get_full_name(self, obj):
        first_name = (getattr(obj, "first_name", "") or "").strip()
        last_name = (getattr(obj, "last_name", "") or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        return full_name or getattr(obj, "email", None) or getattr(obj, "username", "") or str(obj.id)

    def get_is_staff(self, obj):
        return getattr(obj, "role", None) == "admin"

    def get_is_superuser(self, obj):
        return is_super_admin(getattr(obj, "id", 0))


class AdminCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    is_staff = serializers.BooleanField(required=False, default=True)
    is_superuser = serializers.BooleanField(required=False, default=False)

    def validate_email(self, value):
        if exists_admin_email(value):
            raise serializers.ValidationError("User with this email already exists.")
        return value

    def create(self, validated_data):
        email = validated_data["email"]
        first_name = validated_data.get("first_name", "")
        last_name = validated_data.get("last_name", "")

        base_username = (email.split("@")[0] or "admin").strip()
        username = make_unique_admin_username(base_username)

        return create_admin_user(
            email=email,
            username=username,
            first_name=first_name,
            last_name=last_name,
            # Was accepted and silently discarded, leaving every new admin with no
            # password at all — which is half of why login could not check one.
            password=validated_data["password"],
        )
