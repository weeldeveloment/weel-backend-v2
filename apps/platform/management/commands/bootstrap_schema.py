"""Rebuild the database schema from what the repository knows.

Order matters and is not obvious, so it lives here rather than in five places:

  1. extensions   — postgis and vector, which migrations and columns depend on
  2. migrate      — the Django-managed tables (auth, contenttypes, recommendation)
  3. baseline SQL — the raw-SQL tables captured by `dump_raw_schema`
  4. code DDL     — pms_* and b2b_*, which the code creates at runtime anyway

Step 3 is skipped with a warning when no baseline file has been committed yet.
Until one is, a database built by this command is missing every raw table that
only exists in production — `users`, `booking`, `property`, `chat_*` and the
rest — which is why the endpoint smoke suite cannot run in CI.

To produce the baseline, run against staging (or a restored dump):

    python manage.py dump_raw_schema --output schema/public_baseline.sql
"""
from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

DEFAULT_BASELINE = Path("schema/public_baseline.sql")

# `vector` is required by recommendation.0001_initial; `postgis` by the
# property location columns.
REQUIRED_EXTENSIONS = ("postgis", "vector")


class Command(BaseCommand):
    help = "Build the full schema: extensions, migrations, baseline SQL, code DDL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--baseline",
            default=str(DEFAULT_BASELINE),
            help=f"Path to the raw-schema SQL file (default: {DEFAULT_BASELINE}).",
        )
        parser.add_argument(
            "--skip-migrate",
            action="store_true",
            help="Assume Django migrations have already been applied.",
        )

    def handle(self, *args, **options):
        self._create_extensions()

        if not options["skip_migrate"]:
            self.stdout.write("Applying Django migrations...")
            call_command("migrate", "--noinput", verbosity=0)

        self._apply_baseline(Path(options["baseline"]))

        self.stdout.write("Creating pms_* tables in public...")
        call_command("create_tenant_schema", "public")

        self.stdout.write("Creating b2b_* tables...")
        call_command("create_b2b_tables")

        self.stdout.write(self.style.SUCCESS("Schema bootstrap complete."))

    def _create_extensions(self) -> None:
        for extension in REQUIRED_EXTENSIONS:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')
                self.stdout.write(f"  extension: {extension}")
            except Exception as exc:
                # Worth continuing: a developer database without PostGIS can
                # still run most of the suite, and failing here would hide that.
                self.stderr.write(
                    self.style.WARNING(
                        f"  extension '{extension}' unavailable ({exc}). "
                        f"Anything depending on it will fail."
                    )
                )

    def _apply_baseline(self, path: Path) -> None:
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path

        if not path.exists():
            self.stderr.write(
                self.style.WARNING(
                    f"No raw-schema baseline at {path} — skipping.\n"
                    f"  The database will be missing every table that exists only in\n"
                    f"  production (users, booking, property, chat_*, notification, ...).\n"
                    f"  Generate it against staging with:\n"
                    f"    python manage.py dump_raw_schema --output {DEFAULT_BASELINE}"
                )
            )
            return

        self.stdout.write(f"Applying raw-schema baseline from {path}...")
        sql = path.read_text(encoding="utf-8")
        with connection.cursor() as cursor:
            cursor.execute(sql)
        self.stdout.write("  baseline applied")
