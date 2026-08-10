from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.platform.raw_repository import list_organizations
from shared.raw.db import execute, fetch_all, pop_schema_context, push_schema_context, table_exists


class Command(BaseCommand):
    """Replaces amenity guids in pms_property.amenities with the service's name.

    weel-b2b's ObjectCreatePage used to store a service's guid instead of its
    title when an owner ticked an amenity checkbox — the mobile app has no way
    to resolve a guid back to a name, so it showed the raw guid under a hotel's
    amenities. Fixed at the source (the checkbox now stores the title), but
    that only prevents new bad writes; anything saved before the fix is stuck
    with guids until this runs.

    Dry-run by default — reports every change it would make without writing
    any of them. Pass --apply to write.
    """

    help = "Repairs pms_property.amenities rows that hold service guids instead of names."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the fix. Without this flag the command only reports what it would change.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]

        catalog = {
            str(row["id"]).lower(): row["title"]
            for row in fetch_all("SELECT id, title FROM public.services")
            if row.get("title")
        }
        if not catalog:
            self.stdout.write(self.style.WARNING(
                "public.services has no rows — nothing to resolve guids against."
            ))
            return

        total_properties = 0
        total_values = 0

        for org in list_organizations():
            schema = org["schema_name"]
            if not schema or not table_exists("pms_property", schema=schema):
                continue

            push_schema_context(schema)
            try:
                rows = fetch_all(
                    "SELECT id, name, amenities FROM pms_property "
                    "WHERE amenities IS NOT NULL AND array_length(amenities, 1) > 0"
                )
                for row in rows:
                    amenities = row["amenities"] or []
                    fixed = [catalog.get(value.strip().lower(), value) for value in amenities]
                    changed = [(old, new) for old, new in zip(amenities, fixed) if old != new]
                    if not changed:
                        continue

                    total_properties += 1
                    total_values += len(changed)
                    label = f"{schema}.pms_property#{row['id']} ({row['name']!r})"
                    for old, new in changed:
                        self.stdout.write(f"  {label}: {old} -> {new}")

                    if apply_changes:
                        execute(
                            "UPDATE pms_property SET amenities = %s WHERE id = %s",
                            [fixed, row["id"]],
                        )
            finally:
                pop_schema_context()

        verb = "Fixed" if apply_changes else "Would fix"
        noun = "property" if total_properties == 1 else "properties"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {total_values} amenity value(s) across {total_properties} {noun}."
        ))
        if not apply_changes and total_properties:
            self.stdout.write("Re-run with --apply to write these changes.")
