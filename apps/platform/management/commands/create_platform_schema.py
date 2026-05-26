from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Create platform schema tables"

    def handle(self, *args, **options):
        self.stdout.write("Creating platform schema tables...")

        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS platform_organization (
                    id BIGSERIAL PRIMARY KEY,
                    name VARCHAR(200) NOT NULL,
                    slug VARCHAR(100) NOT NULL UNIQUE,
                    schema_name VARCHAR(100) NOT NULL UNIQUE,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created platform_organization")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS platform_user (
                    id BIGSERIAL PRIMARY KEY,
                    email VARCHAR(254) NOT NULL UNIQUE,
                    phone VARCHAR(32),
                    password_hash VARCHAR(255) NOT NULL,
                    first_name VARCHAR(100),
                    last_name VARCHAR(100),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
            self.stdout.write("  Created platform_user")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS platform_organization_member (
                    id BIGSERIAL PRIMARY KEY,
                    organization_id BIGINT NOT NULL REFERENCES platform_organization(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL REFERENCES platform_user(id) ON DELETE CASCADE,
                    role VARCHAR(20) NOT NULL DEFAULT 'manager',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(organization_id, user_id)
                );
            """)
            self.stdout.write("  Created platform_organization_member")

        self.stdout.write(self.style.SUCCESS("Platform schema tables created successfully."))
