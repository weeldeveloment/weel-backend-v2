from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("property", "0005_ensure_apartment_number_column"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE property_propertydetail "
                "ADD COLUMN IF NOT EXISTS home_number varchar(50) NULL, "
                "ADD COLUMN IF NOT EXISTS entrance_number varchar(50) NULL, "
                "ADD COLUMN IF NOT EXISTS floor_number varchar(50) NULL, "
                "ADD COLUMN IF NOT EXISTS pass_code varchar(50) NULL;"
            ),
            reverse_sql=(
                "ALTER TABLE property_propertydetail "
                "DROP COLUMN IF EXISTS pass_code, "
                "DROP COLUMN IF EXISTS floor_number, "
                "DROP COLUMN IF EXISTS entrance_number, "
                "DROP COLUMN IF EXISTS home_number;"
            ),
        ),
    ]
