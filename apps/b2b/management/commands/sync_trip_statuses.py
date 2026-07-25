"""
Trip status kunlik sana bo'yicha avtomatik yangilanishi kerak bo'lgan
joyni qo'lda ishga tushirish uchun (odatda celery beat buni har kuni
avtomatik bajaradi, bu esa uni darhol/qo'lda triggerlash imkonini beradi).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.b2b.repository import sync_trip_statuses_for_date


class Command(BaseCommand):
    help = "Trip statuslarini joriy sanaga qarab yangilaydi (pending/draft -> active -> completed)."

    def handle(self, *args, **options):
        today = timezone.localdate()
        updated = sync_trip_statuses_for_date(today)
        self.stdout.write(self.style.SUCCESS(f"{updated} ta trip statusi yangilandi ({today})."))
