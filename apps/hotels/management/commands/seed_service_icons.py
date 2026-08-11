from __future__ import annotations

from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from shared.raw.db import execute, fetch_all, fetch_one

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

# service title -> the service whose icon it borrows. These amenities mean the
# same thing as one already in the catalogue, so they reuse that file instead
# of a near-duplicate drawing.
BORROWED_BY_TITLE = {
    # Reception & security
    "Video surveillance": "Security cameras",
    "24-hour security": "Security cameras",
    "Alarm system": "Security cameras",
    # Transport & parking
    "Parkovka": "Garage",
    "Surface parking": "Garage",
    "Covered parking": "Garage",
    "Disabled parking": "Garage",
    "Car rental": "Charging for electric vehicles",
    "Free airport transfer": "Charging for electric vehicles",
    "Paid airport transfer": "Charging for electric vehicles",
    "Shuttle service": "Charging for electric vehicles",
    "Ski shuttle": "Charging for electric vehicles",
    # Food & drink
    "Restaurant": "Tableware and cutlery",
    "Diner/Snack bar": "Tableware and cutlery",
    "Breakfast in room": "Tableware and cutlery",
    "Children's menu": "Tableware and cutlery",
    "Special menu": "Tableware and cutlery",
    "Vegetarian/Vegan menu": "Tableware and cutlery",
    "On-site coffee shop": "Coffee machine",
    "Shared kitchen": "Fully equipped kitchen",
    "Water in room": "Water filter",
    # Pools & bathhouse
    "Indoor pool": "Winter pool",
    "Outdoor pool": "Summer pool",
    "Rooftop pool": "Summer pool",
    "Infinity pool": "Summer pool",
    "Children's pool": "Summer pool",
    "Shared pool": "Summer pool",
    "Water park": "Summer pool",
    "Public bathhouse": "Sauna / steam room",
    "Hammam": "Sauna / steam room",
    "SPA center": "Sauna / steam room",
    "Relaxation area": "Outdoor recreation area",
    # Fitness
    "Fitness center": "Gym",
    "Personal trainer": "Gym",
    "Tennis court": "Table tennis",
    "Mini golf": "Golf",
    # Entertainment
    "Live music": "Karaoke",
    "Night club/DJ": "Karaoke",
    "Film screenings": "Home Cinema",
    "Sports broadcast": "Smart TV",
    "Play room": "Table games",
    "Evening entertainment": "Entertainments",
    "Cooking classes": "Pots and pans",
    # For children
    "Children's TV": "Smart TV",
    "Playground": "Outdoor recreation area",
    "Family rooms": "The cot",
    "Kids' club": "Table games",
    # Services
    "Ironing": "Iron",
    "Laundry": "Washer",
    "Dry cleaning": "Drying machine",
    "Daily cleaning": "Vacuum cleaner",
    "Business center": "Workplace",
    "Conference facilities": "Workplace",
}


def _has_icon(icon_url: str) -> bool:
    return bool(icon_url) and "default.svg" not in icon_url


def _update_icon_sql() -> str:
    """`public.services` carries an `updated_at` column on some deployments and
    not others — it is a raw-SQL table, not a migrated model, so the schema
    drifted. Writing to a column that isn't there aborts the whole command, so
    ask the catalogue first rather than assuming."""
    has_updated_at = fetch_one(
        "SELECT 1 AS present FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'services' "
        "AND column_name = 'updated_at'"
    )
    if has_updated_at:
        return "UPDATE public.services SET icon_url = %s, updated_at = NOW() WHERE title = %s"
    return "UPDATE public.services SET icon_url = %s WHERE title = %s"


class Command(BaseCommand):
    """Gives every amenity an icon.

    `seed_services_category.sql` created all 87 hotel-specific services with
    'property/icons/default.svg' hardcoded, so hotel amenities render as a
    blank placeholder while apartments and cottages look fine. Two ways out,
    both applied here: amenities that duplicate a concept already in the
    catalogue borrow its icon, and the rest — accessibility aids, security
    equipment, spa services — get the drawings bundled in `assets/`.

    Only rows still on the default icon are repointed, so a service given real
    artwork by hand is left alone and re-running is a no-op. That is what makes
    it safe for entrypoint.sh to run on every deploy. Dry-run by default —
    pass --apply to upload and write.
    """

    help = "Gives amenity services an icon: uploads the bundled ones, borrows for the rest."

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

        update_sql = _update_icon_sql()
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

            execute(update_sql, [storage_path, title])
            linked += 1

        borrowed = 0
        for title, source_title in BORROWED_BY_TITLE.items():
            if title not in existing:
                missing_service.append(title)
                continue
            if _has_icon(existing[title]) and not force:
                already_set.append(title)
                continue
            source_icon = existing.get(source_title, "")
            if not _has_icon(source_icon):
                # The catalogue entry it would borrow from has no icon either.
                continue

            self.stdout.write(f"  {title} -> {source_icon} (borrowed from {source_title})")
            borrowed += 1
            if apply_changes:
                execute(update_sql, [source_icon, title])

        verb = "Uploaded" if apply_changes else "Would upload"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {uploaded} icon(s), linked {linked} service(s), "
                f"borrowed an existing icon for {borrowed} more."
            )
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
