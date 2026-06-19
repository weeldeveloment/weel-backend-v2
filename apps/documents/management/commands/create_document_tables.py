from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Create Document tables in the public schema"

    def handle(self, *args, **options):
        self.stdout.write("Creating document tables...")

        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS document (
                    id BIGSERIAL PRIMARY KEY,
                    doc_number VARCHAR(50) NOT NULL UNIQUE,
                    doc_type VARCHAR(30) NOT NULL DEFAULT 'invoice',
                    organization_id BIGINT,
                    company_id BIGINT,
                    partner_id BIGINT,
                    booking_id BIGINT,
                    trip_id BIGINT,
                    amount NUMERIC(14,2),
                    status VARCHAR(20) NOT NULL DEFAULT 'created',
                    pdf_url VARCHAR(500),
                    notes TEXT,
                    created_by BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created document")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS document_recipient (
                    id BIGSERIAL PRIMARY KEY,
                    document_id BIGINT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
                    recipient_type VARCHAR(20) NOT NULL DEFAULT 'client',
                    inn VARCHAR(20),
                    org_name VARCHAR(300),
                    bank_details JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created document_recipient")

        self.stdout.write(self.style.SUCCESS("Document tables created successfully."))
