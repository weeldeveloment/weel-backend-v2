from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("property", "0004_category_district_mahalla_region_property_img_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "ALTER TABLE property_propertydetail "
                "ADD COLUMN IF NOT EXISTS apartment_number varchar(50) NULL;"
            ),
            reverse_sql=(
                "ALTER TABLE property_propertydetail "
                "DROP COLUMN IF EXISTS apartment_number;"
            ),
        ),
    ]
