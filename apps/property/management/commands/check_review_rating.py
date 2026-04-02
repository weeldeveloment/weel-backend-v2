"""
Review va reytingni tekshirish: 5 balli review qaysi dachada va u API roʻyxatida chiqadimi?
Ishlatish: python manage.py check_review_rating
"""
from django.db.models import Q
from django.core.management.base import BaseCommand
from property.models import Property, PropertyReview


class Command(BaseCommand):
    help = "5 balli review qaysi propertyda ekanligini va u tekshirilgan roʻyxatda bor-yoʻqligini koʻrsatadi."

    def handle(self, *args, **options):
        reviews = PropertyReview.objects.filter(
            rating__gte=4,
            rating__isnull=False,
        ).filter(Q(is_hidden=False) | Q(is_hidden__isnull=True)).select_related("property")

        if not reviews.exists():
            self.stdout.write(
                self.style.WARNING(
                    "4+ balli va yashirin boʻlmagan review topilmadi. "
                    "Admin da Hide ni unchecked qiling va rating ni tekshiring."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f"4+ balli (yashirin emas) reviewlar: {reviews.count()} ta\n")
        )
        for r in reviews:
            p = r.property
            in_list = p.is_verified and not p.is_archived
            status = "✓ API roʻyxatida chiqadi" if in_list else "✗ API roʻyxatida YOʻQ (tekshirilmagan yoki arxiv)"
            self.stdout.write(
                f"  Property: {p.title} (guid={p.guid})\n"
                f"    Reyting: {r.rating}, Hide: {r.is_hidden}\n"
                f"    {status}\n"
            )
            if not in_list:
                self.stdout.write(
                    self.style.WARNING(
                        "    → Bu dachani API da koʻrish uchun Admin → Property → "
                        "«Test» ni oching va Verified (Tekshirilgan) qiling."
                    )
                )
