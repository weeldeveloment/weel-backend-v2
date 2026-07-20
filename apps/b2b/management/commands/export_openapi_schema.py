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
