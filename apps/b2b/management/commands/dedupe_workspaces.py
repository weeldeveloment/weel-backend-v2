"""`manage.py dedupe_workspaces [--dry-run]` — the start-up cleanup of
`apps.b2b.workspace.dedupe`, by hand. `--dry-run` says what it would merge
and retire without writing anything."""
from django.core.management.base import BaseCommand

from apps.b2b.workspace import dedupe


class Command(BaseCommand):
    help = "Merge one person's duplicate seats and retire empty copies of workspaces"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        report = dedupe.run(dry_run=options["dry_run"], log=self.stdout.write)
        self.stdout.write(
            "{seats_merged} seat(s) merged, {workspaces_retired} workspace(s) and "
            "{companies_retired} company(ies) retired, {left_for_a_person} left for "
            "a person to decide".format(**report)
            + (" (dry run — nothing written)" if options["dry_run"] else "")
        )
