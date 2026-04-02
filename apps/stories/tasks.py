from celery import shared_task

from django.core.cache import cache

from .raw_repository import increment_story_views


@shared_task()
def persist_story_views():
    keys = cache.iter_keys("story:*:views")

    for key in keys:
        guid = key.split(":")[1]
        count = cache.get(key)
        if not count:
            continue

        updated = increment_story_views(guid, int(count))
        if updated:
            cache.delete(key)
