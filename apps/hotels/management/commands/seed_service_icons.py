from __future__ import annotations

from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from shared.raw.db import execute, fetch_all

ICON_DIR = Path(__file__).resolve().parents[2] / "assets" / "service_icons"
ICON_STORAGE_PREFIX = "property/icons"

# service title -> icon file in ICON_DIR. Titles are the English `services.title`
# values, which is what the seed migration wrote and what the admin lists.
ICONS_BY_TITLE = {
    # Accessibility
    "Braille signs": "braille-signs",
    "Disabled rooms": "disabled-rooms",
    "Emergency cord": "emergency-cord",
    "Handrails": "handrails",
    "High toilet": "high-toilet",
    "Hypo-allergenic rooms": "hypo-allergenic-rooms",
    "Low sink": "low-sink",
    "Wheelchair accessible": "wheelchair-accessible",
    # Entertainment
    "Guided tours": "guided-tours",
    "Stand-up comedy": "stand-up-comedy",
    "Themed dinners": "themed-dinners",
    # Fitness
    "Badminton": "badminton",
    "Bicycles": "bicycles",
    "Fishing": "fishing",
    "Hiking": "hiking",
    "Horse riding": "horse-riding",
    "Yoga": "yoga",
    # Food & drink
    "Bar": "bar",
    "Grocery store": "grocery-store",
    "Vending machine": "vending-machine",
    # For children
    "Baby food": "baby-food",
    "Strollers": "strollers",
    # Outdoor
    "Barbekyu": "barbekyu",
    # Reception & security
    "24-hour front desk": "24-hour-front-desk",
    "Carbon monoxide sensor": "carbon-monoxide-sensor",
    "Contactless check-in/out": "contactless-check-in-out",
    "Express check-in/out": "express-check-in-out",
    "Fire extinguishers": "fire-extinguishers",
    "Key/card access": "key-card-access",
    "Smoke detectors": "smoke-detectors",
    # Services
    "ATM": "atm",
    "Concierge": "concierge",
    "Currency exchange": "currency-exchange",
    "Elevator": "elevator",
    "Shoe cleaning": "shoe-cleaning",
    "Tour desk": "tour-desk",
    # Spa & wellness
    "Body treatments": "body-treatments",
    "Hairdresser": "hairdresser",
    "Manicure/Pedicure": "manicure-pedicure",
    "Massage": "massage",
    "Solarium": "solarium",
    # Transport & parking
    "Luggage storage": "luggage-storage",
}


class Command(BaseCommand):
    """Uploads the bundled amenity icons and points their services at them.

    `seed_services_category.sql` created every hotel-specific service with
    'property/icons/default.svg', so those amenities render as a blank
    placeholder. `fix_hotel_service_icons.sql` covers the ones that can borrow
    an existing icon; this covers the rest, which had no equivalent to borrow
    and needed drawing.

    Only rows still on the default icon are repointed, so a service given real
    artwork by hand is left alone and re-running is a no-op. Dry-run by
    default — pass --apply to upload and write.
    """

    help = "Uploads bundled amenity icons and links them to their services."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Upload and write. Without it the command only reports what it would do.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Repoint services that already have a non-default icon too.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        force = options["force"]

        existing = {
            str(row["title"]): str(row.get("icon_url") or "")
            for row in fetch_all("SELECT title, icon_url FROM public.services")
        }

        uploaded = 0
        linked = 0
        missing_service: list[str] = []
        already_set: list[str] = []

        for title, icon_name in ICONS_BY_TITLE.items():
            source = ICON_DIR / f"{icon_name}.svg"
            if not source.exists():
                self.stderr.write(self.style.ERROR(f"icon file missing: {source}"))
                continue

            if title not in existing:
                missing_service.append(title)
                continue

            current = existing[title]
            if current and "default.svg" not in current and not force:
                already_set.append(title)
                continue

            storage_path = f"{ICON_STORAGE_PREFIX}/{icon_name}.svg"
            self.stdout.write(f"  {title} -> {storage_path}")

            if not apply_changes:
                uploaded += 1
                linked += 1
                continue

            # Overwrite rather than letting storage suffix a duplicate name, so
            # re-running keeps pointing at the same object instead of piling up
            # icon_a1b2c3.svg copies next to it.
            if default_storage.exists(storage_path):
                default_storage.delete(storage_path)
            default_storage.save(storage_path, ContentFile(source.read_bytes()))
            uploaded += 1

            execute(
                "UPDATE public.services SET icon_url = %s, updated_at = NOW() "
                "WHERE title = %s",
                [storage_path, title],
            )
            linked += 1

        verb = "Uploaded" if apply_changes else "Would upload"
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {uploaded} icon(s), linked {linked} service(s).")
        )
        if already_set:
            self.stdout.write(
                f"Left alone (already has an icon): {', '.join(sorted(already_set))}"
            )
        if missing_service:
            self.stdout.write(
                self.style.WARNING(
                    f"No such service in public.services: {', '.join(sorted(missing_service))}"
                )
            )
        if not apply_changes:
            self.stdout.write("Re-run with --apply to upload and write.")
