from django.core.management.base import BaseCommand

from apps.platform.raw_repository import create_tenant_schema


class Command(BaseCommand):
    """Provision a tenant schema by hand.

    This used to carry its own copy of the DDL, and the two copies drifted:
    the command created the four `pms_bookingcom_*` tables and six external-
    reference columns on `pms_booking` that the runtime path lacked, while the
    runtime path created `pms_room_type` and `pms_room.room_type_id` that the
    command lacked. Whichever way a tenant was provisioned, some feature was
    querying a table or column that did not exist for it.

    There is now one definition — `raw_repository.create_tenant_schema`, the
    same function registration calls — and this command is a thin wrapper.
    """

    help = "Create a tenant schema with the PMS tables"

    def add_arguments(self, parser):
        parser.add_argument("schema_name", type=str, help="Name of the tenant schema")

    def handle(self, *args, **options):
        schema_name = options["schema_name"]
        self.stdout.write(f"Creating tenant schema: {schema_name}...")

        create_tenant_schema(schema_name)

        self.stdout.write(
            self.style.SUCCESS(f"Tenant schema '{schema_name}' created successfully.")
        )
