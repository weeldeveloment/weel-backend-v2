"""Wipes the sales funnel — every lead on the board and everything hanging off
it — so the screen can be reopened on real data instead of the rows that were
typed into it while it was being built.

Deliberately a command rather than a migration: it destroys data, so it runs
when somebody decides it should, names the company it is about, and says out
loud what it is going to remove before it removes it.

    python manage.py purge_leads                 # counts only, deletes nothing
    python manage.py purge_leads --yes           # every company on this server
    python manage.py purge_leads --company 3 --yes

The lead's items and its activity go with it (ON DELETE CASCADE) and a task
raised off a lead is left standing with its `lead_id` cleared (ON DELETE SET
NULL) — that is the schema's own rule, not something re-decided here, so a
purge cannot silently take a week of somebody's tasks with it.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.b2b.raw.tables import (
    B2B_WORKSPACE_LEAD_ACTIVITY_TABLE,
    B2B_WORKSPACE_LEAD_ITEM_TABLE,
    B2B_WORKSPACE_LEAD_TABLE,
)
from shared.raw.db import execute, fetch_one, table_exists


class Command(BaseCommand):
    help = (
        "Deletes the workspace sales funnel (b2b_workspace_lead and its items, "
        "activity). Dry run unless --yes is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company",
            type=int,
            default=None,
            help="Only this company's leads. Omit to purge every company.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Actually delete. Without it the command only counts.",
        )

    def handle(self, *args, **options):
        company_id = options["company"]
        where, params = ("WHERE company_id = %s", [company_id]) if company_id else ("", [])
        scope = f"company {company_id}" if company_id else "every company"

        counts = {
            "leads": self._count(B2B_WORKSPACE_LEAD_TABLE, where, params),
            "items": self._count_child(B2B_WORKSPACE_LEAD_ITEM_TABLE, where, params),
            "activity": self._count_child(
                B2B_WORKSPACE_LEAD_ACTIVITY_TABLE, where, params
            ),
        }
        for name, count in counts.items():
            self.stdout.write(f"  {count} {name}")

        if not counts["leads"]:
            self.stdout.write(self.style.SUCCESS(f"Nothing to purge for {scope}."))
            return

        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run — nothing deleted. Re-run with --yes to purge {scope}."
                )
            )
            return

        # One transaction: a half-emptied funnel is worse than a full one.
        with transaction.atomic():
            deleted = execute(
                f"DELETE FROM {B2B_WORKSPACE_LEAD_TABLE} {where}", params
            )
        self.stdout.write(
            self.style.SUCCESS(f"Purged {deleted} leads from {scope}.")
        )

    def _count(self, table: str, where: str, params: list) -> int:
        row = fetch_one(f"SELECT COUNT(*) AS n FROM {table} {where}", params)
        return int(row["n"]) if row else 0

    def _count_child(self, table: str, where: str, params: list) -> int:
        """Children are counted through their lead, so `--company` narrows them
        the same way it narrows the board.

        A server whose schema predates the items/activity tables reports zero
        rather than crashing: the point of the command is the board, and it
        cannot fail to empty it because a table it only counts is missing.
        """
        if not table_exists(table):
            return 0
        row = fetch_one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE lead_id IN "
            f"(SELECT id FROM {B2B_WORKSPACE_LEAD_TABLE} {where})",
            params,
        )
        return int(row["n"]) if row else 0
