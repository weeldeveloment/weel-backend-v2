from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("stories", "0002_remove_storyview_unique_story_client_story_view_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="story",
            name="is_platform_news",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="story",
            name="title",
            field=models.TextField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="story",
            name="body",
            field=models.TextField(null=True, blank=True),
        ),
    ]
