import json
from pathlib import Path

from django.core.management.base import BaseCommand
from drf_yasg import openapi

from core.swagger_hooks import RequiredFixOpenAPISchemaGenerator


class Command(BaseCommand):
    help = "Export the OpenAPI schema to a JSON file for frontend type generation"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default="openapi.json",
            help="Output file path (default: openapi.json)",
        )
        parser.add_argument(
            "--format",
            type=str,
            choices=["json", "yaml"],
            default="json",
            help="Output format (default: json)",
        )
        parser.add_argument(
            "--variant",
            type=str,
            choices=["main", "b2b"],
            default="main",
            help="Schema variant: main (all endpoints) or b2b (B2B/Hotels/Documents only)",
        )
        parser.add_argument(
            "--base-path",
            type=str,
            default=None,
            help=(
                "Keep only paths under this prefix, strip it, and record it as "
                "basePath (e.g. /api). Clients that already point at the prefix "
                "would otherwise generate /api/api/... URLs. Most routes are "
                "registered both with and without the prefix, so this also drops "
                "the duplicates."
            ),
        )

    HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

    @classmethod
    def _disambiguate_operation_ids(cls, schema_dict):
        """Make every operationId unique.

        Several views are mounted at more than one URL (apartments and cottages
        share a view, and so carry one hardcoded operation_id between them).
        OpenAPI requires operationIds to be unique, and client generators emit
        one function per operationId — duplicates collide into code that does
        not compile. Each colliding operation is suffixed with the path segment
        that tells it apart from its twins.
        """
        groups = {}
        for path, operations in schema_dict.get("paths", {}).items():
            for method, operation in operations.items():
                if method not in cls.HTTP_METHODS or not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId")
                if operation_id:
                    groups.setdefault(operation_id, []).append((path, operation))

        renamed = 0
        for operation_id, members in groups.items():
            if len(members) < 2:
                continue

            segment_sets = [
                {s for s in path.split("/") if s and not s.startswith("{")}
                for path, _ in members
            ]
            for index, (path, operation) in enumerate(members):
                others = set().union(*(segment_sets[:index] + segment_sets[index + 1:]))
                distinctive = sorted(segment_sets[index] - others)
                suffix = (
                    "".join(part.capitalize() for part in distinctive[0].split("-"))
                    if distinctive
                    else str(index + 1)
                )
                operation["operationId"] = f"{operation_id}{suffix}"
                renamed += 1

        return renamed

    def handle(self, *args, **options):
        from django.conf import settings

        variant = options["variant"]

        if variant == "b2b":
            from core.urls import _b2b_patterns

            info = openapi.Info(
                "Weel B2B API",
                "v1",
                "B2B Corporate Travel Management — Business Trips, Documents, Hotel Catalog, Admin",
                contact=openapi.Contact(name="Weel Support", url="https://weel.uz"),
                license=openapi.License(name="Proprietary"),
            )
            patterns = _b2b_patterns
        else:
            from core.urls import schema_info as info
            patterns = None

        schema_generator = RequiredFixOpenAPISchemaGenerator(
            info=info,
            url=getattr(settings, "SWAGGER_URL", None),
            patterns=patterns,
        )

        schema = schema_generator.get_schema(request=None, public=True)

        output_path = Path(options["output"])
        output_path.parent.mkdir(parents=True, exist_ok=True)

        schema_dict = schema.as_dict()

        base_path = (options.get("base_path") or "").rstrip("/")
        if base_path:
            schema_dict["basePath"] = base_path
            schema_dict["paths"] = {
                path[len(base_path):]: operations
                for path, operations in schema_dict.get("paths", {}).items()
                if path.startswith(base_path + "/")
            }

            # Auto-generated operationIds are built from the full path, so they
            # all carry the prefix too ("api_pms_properties_list"). Client
            # generators turn those into method names, so leaving the prefix in
            # would rename every method the moment a base path is used.
            id_prefix = base_path.lstrip("/").replace("/", "_") + "_"
            for operations in schema_dict["paths"].values():
                for operation in operations.values():
                    if not isinstance(operation, dict):
                        continue
                    operation_id = operation.get("operationId", "")
                    if operation_id.startswith(id_prefix):
                        operation["operationId"] = operation_id[len(id_prefix):]

        renamed = self._disambiguate_operation_ids(schema_dict)

        if options["format"] == "yaml":
            import yaml
            content = yaml.dump(schema_dict, default_flow_style=False, allow_unicode=True)
            output_path.write_text(content, encoding="utf-8")
        else:
            content = json.dumps(schema_dict, indent=2, ensure_ascii=False)
            output_path.write_text(content, encoding="utf-8")

        path_count = len(schema_dict.get("paths", {}))
        self.stdout.write(
            self.style.SUCCESS(
                f"OpenAPI schema ({variant}) exported to {output_path.resolve()} — {path_count} paths"
            )
        )
        if renamed:
            self.stdout.write(
                self.style.WARNING(
                    f"{renamed} operations had duplicate operationIds and were suffixed to make them unique."
                )
            )
