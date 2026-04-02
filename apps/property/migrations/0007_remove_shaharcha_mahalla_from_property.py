from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("property", "0006_ensure_propertydetail_extra_columns"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="property",
            name="mahalla",
        ),
        migrations.RemoveField(
            model_name="property",
            name="shaharcha",
        ),
        migrations.DeleteModel(
            name="Mahalla",
        ),
        migrations.DeleteModel(
            name="Shaharcha",
        ),
    ]
