import inspect

from drf_yasg.generators import OpenAPISchemaGenerator


def _is_field_required(field):
    from rest_framework.fields import empty

    if getattr(field, "read_only", False):
        return False
    if getattr(field, "write_only", False):
        return False
    if getattr(field, "allow_null", False):
        return False
    default = getattr(field, "default", empty)
    if default is not empty:
        if default in (list, dict):
            return True
        if default is not False and default != "":
            return False
    return getattr(field, "required", True)


def _is_response_required(field):
    if not getattr(field, "read_only", False):
        return False
    return True


def _get_all_serializer_instances():
    import importlib
    modules_to_scan = [
        "apps.b2b.serializers",
        "apps.b2b.raw_serializers",
        "apps.hotels.serializers",
        "apps.property.hotel_serializers",
        "apps.pms.serializers",
        "apps.pms.raw_serializers",
        "apps.documents.serializers",
        "apps.property.serializers",
        "apps.booking.raw_serializers",
        "apps.booking.serializers",
        "apps.users.serializers",
        "apps.platform.serializers",
        "apps.notification.serializers",
        "apps.activities.serializers",
        "apps.stories.serializers",
        "apps.chat.serializers",
        "apps.admin_auth.serializers",
        "apps.admin_auth.hotel_serializers",
        "apps.recommendation.serializers",
        "apps.shared.serializers",
        "apps.payment.serializers",
    ]

    serializers = {}
    for module_path in modules_to_scan:
        try:
            module = importlib.import_module(module_path)
        except ImportError:
            continue
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if not name.endswith("Serializer"):
                continue
            if name in ("Serializer", "ModelSerializer"):
                continue
            try:
                instance = obj()
                ref = name
                if ref.endswith("Serializer"):
                    ref = ref[: -len("Serializer")]
                serializers[ref] = instance
            except Exception:
                pass
    return serializers


class RequiredFixOpenAPISchemaGenerator(OpenAPISchemaGenerator):
    _serializer_registry = None

    @classmethod
    def _get_registry(cls):
        if cls._serializer_registry is None:
            cls._serializer_registry = _get_all_serializer_instances()
        return cls._serializer_registry

    def get_schema(self, request=None, public=False):
        schema = super().get_schema(request=request, public=public)
        registry = self._get_registry()

        definitions = schema.definitions
        if not definitions:
            return schema

        for def_name, def_schema in definitions.items():
            properties = getattr(def_schema, "properties", None)
            if not properties:
                continue

            serializer = registry.get(def_name)
            if serializer is None:
                continue

            required = []
            for field_name, field in serializer.fields.items():
                if field_name not in properties:
                    continue
                if _is_field_required(field) or _is_response_required(field):
                    required.append(field_name)

            existing = list(getattr(def_schema, "required", []) or [])
            if set(required) != set(existing):
                if required:
                    def_schema.required = required
                elif hasattr(def_schema, "required"):
                    try:
                        delattr(def_schema, "required")
                    except AttributeError:
                        def_schema.required = None

        return schema
