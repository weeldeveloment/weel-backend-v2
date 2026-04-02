import logging
import uuid
from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import resolve, reverse, Resolver404
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIRequestFactory, APIClient, force_authenticate

from booking.serializers import PropertyBookingSerializer
from payment.models import ExchangeRate
from property.serializers import (
    PropertyPatchSerializer,
    PropertyImageCreateSerializer,
    PropertyTypeListSerializer,
    PropertyTypeSlugRelatedField,
)
from property.views import (
    PropertyListCreateView,
    ApartmentPropertyListCreateView,
    CottagePropertyListCreateView,
    PropertyTypeListView,
    PropertyServiceListView,
    PropertyRetrieveUpdateDestroyView,
    PartnerPropertyListView,
)
from property.models import (
    Property,
    PropertyType,
    PropertyRoom,
    PropertyImage,
    PropertyDetail,
    PropertyLocation,
    PropertyReview,
    PropertyService,
    PropertyPrice,
    Region,
    District,
    VerificationStatus,
)
from property.filters import PropertyFilter, PropertyServiceFilter
from users.models import Partner, Client

logging.getLogger("django.request").setLevel(logging.ERROR)


class PropertyUrlTests(TestCase):
    def test_properties_with_trailing_slash_resolves(self):
        match = resolve("/api/property/properties/")

        self.assertIs(match.func.view_class, PropertyListCreateView)

    def test_properties_without_trailing_slash_does_not_resolve(self):
        with self.assertRaises(Resolver404):
            resolve("/api/property/properties")

    def test_apartments_endpoint_resolves(self):
        match = resolve("/api/property/properties/apartments/")

        self.assertIs(match.func.view_class, ApartmentPropertyListCreateView)

    def test_cottages_endpoint_resolves(self):
        match = resolve("/api/property/properties/cottages/")

        self.assertIs(match.func.view_class, CottagePropertyListCreateView)


@override_settings(MEDIA_ROOT="/tmp/weel-test-media")
class PropertyVerificationRegressionTests(TestCase):
    def _svg_file(self, name="icon.svg"):
        return SimpleUploadedFile(
            name,
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )

    def _image_file(self, name="photo.jpg"):
        try:
            from PIL import Image
            import io
            buf = io.BytesIO()
            Image.new("RGB", (1, 1), color="red").save(buf, format="JPEG")
            buf.seek(0)
            return SimpleUploadedFile(
                name,
                buf.read(),
                content_type="image/jpeg",
            )
        except Exception:
            return SimpleUploadedFile(
                name,
                b"fake-image-bytes",
                content_type="image/jpeg",
            )

    def _create_partner(self):
        suffix = uuid.uuid4().hex[:8]
        return Partner.objects.create(
            first_name="John",
            last_name="Doe",
            username=f"partner_{suffix}",
            phone_number=f"+99890{uuid.uuid4().int % 10**7:07d}",
        )

    def _create_verified_property_bundle(self):
        partner = self._create_partner()
        property_type = PropertyType.objects.create(
            title_en="Apartment",
            title_ru="Apartment ru",
            title_uz="Kvartira",
            icon=self._svg_file(),
        )
        location = PropertyLocation.objects.create(
            latitude="41.2995",
            longitude="69.2401",
            city="Tashkent",
            country="Uzbekistan",
        )
        property_obj = Property.objects.create(
            title=f"Test property {uuid.uuid4().hex[:6]}",
            price="100.00",
            currency="USD",
            property_type=property_type,
            property_location=location,
            partner=partner,
            verification_status=VerificationStatus.ACCEPTED,
        )
        detail = PropertyDetail.objects.create(
            property=property_obj,
            description_en="Description",
            description_ru="Description ru",
            description_uz="Tavsif",
        )
        PropertyRoom.objects.create(property=property_obj, guests=2, rooms=1, beds=1, bathrooms=1)
        return property_obj, detail, partner

    def _ensure_property_list_context(self):
        PropertyType.objects.create(
            title_en="Cottages",
            title_ru="Cottages ru",
            title_uz="Cottages uz",
            icon=self._svg_file("cottages.svg"),
        )
        ExchangeRate.objects.create(
            currency="USD",
            rate="12000.000000",
        )

    def _create_property_for_list(
        self,
        partner,
        *,
        is_verified: bool,
        region: Region | None = None,
        district: District | None = None,
    ):
        property_type = PropertyType.objects.create(
            title_en=f"Apartment {uuid.uuid4().hex[:6]}",
            title_ru="Apartment ru",
            title_uz="Kvartira",
            icon=self._svg_file(f"apartment_{uuid.uuid4().hex[:6]}.svg"),
        )
        location = PropertyLocation.objects.create(
            latitude="41.2995",
            longitude="69.2401",
            city="Tashkent",
            country="Uzbekistan",
        )
        property_obj = Property.objects.create(
            title=f"List property {uuid.uuid4().hex[:6]}",
            price="100.00",
            currency="USD",
            property_type=property_type,
            property_location=location,
            partner=partner,
            region=region,
            district=district,
            verification_status=VerificationStatus.ACCEPTED if is_verified else VerificationStatus.WAITING,
        )
        PropertyRoom.objects.create(
            property=property_obj,
            guests=2,
            rooms=1,
            beds=1,
            bathrooms=1,
        )
        return property_obj

    def test_property_update_keeps_pending_images_pending(self):
        property_obj, detail, _ = self._create_verified_property_bundle()

        image = PropertyImage.objects.create(
            property=property_obj,
            image=self._image_file("existing.jpg"),
            order=1,
            is_pending=False,
        )
        PropertyImage.objects.filter(pk=image.pk).update(is_pending=True)

        serializer = PropertyPatchSerializer(
            detail,
            data={"title": "Updated Property Title"},
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        property_obj.refresh_from_db()
        image.refresh_from_db()

        self.assertFalse(property_obj.is_verified)
        self.assertTrue(image.is_pending)

    def test_new_image_upload_forces_property_reverification(self):
        property_obj, _, partner = self._create_verified_property_bundle()
        request = APIRequestFactory().post("/api/property/properties/images/")
        request.user = partner

        serializer = PropertyImageCreateSerializer(
            data={"images": [self._image_file("new.jpg")]},
            context={
                "request": request,
                "property_id": property_obj.guid,
            },
        )
        serializer.is_valid(raise_exception=True)
        created_images = serializer.save()

        property_obj.refresh_from_db()
        self.assertFalse(property_obj.is_verified)
        self.assertEqual(len(created_images), 1)
        self.assertTrue(created_images[0].is_pending)

    def test_property_price_change_syncs_all_monthly_price_rows(self):
        property_obj, _, _ = self._create_verified_property_bundle()
        from shared.date import month_start, month_end

        today = timezone.localdate()
        current_month_start = month_start(today)
        next_month_start = month_start(current_month_start + timedelta(days=32))

        current_row = PropertyPrice.objects.create(
            property=property_obj,
            month_from=current_month_start,
            month_to=month_end(current_month_start),
            price_on_working_days=Decimal("100"),
            price_on_weekends=Decimal("120"),
            price_per_person=Decimal("10"),
        )
        next_row = PropertyPrice.objects.create(
            property=property_obj,
            month_from=next_month_start,
            month_to=month_end(next_month_start),
            price_on_working_days=Decimal("200"),
            price_on_weekends=Decimal("220"),
            price_per_person=Decimal("15"),
        )

        property_obj.price = Decimal("350")
        property_obj.save(update_fields=["price"])

        current_row.refresh_from_db()
        next_row.refresh_from_db()
        self.assertEqual(current_row.price_on_working_days, Decimal("350"))
        self.assertEqual(current_row.price_on_weekends, Decimal("350"))
        self.assertEqual(next_row.price_on_working_days, Decimal("350"))
        self.assertEqual(next_row.price_on_weekends, Decimal("350"))

    def test_property_price_row_change_syncs_scalar_property_price(self):
        property_obj, _, _ = self._create_verified_property_bundle()
        from shared.date import month_start, month_end

        current_month_start = month_start(timezone.localdate())
        row = PropertyPrice.objects.create(
            property=property_obj,
            month_from=current_month_start,
            month_to=month_end(current_month_start),
            price_on_working_days=Decimal("100"),
            price_on_weekends=Decimal("100"),
            price_per_person=Decimal("0"),
        )

        row.price_on_working_days = Decimal("730")
        row.price_on_weekends = Decimal("710")
        row.save(update_fields=["price_on_working_days", "price_on_weekends"])

        property_obj.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(property_obj.price, Decimal("730"))
        self.assertEqual(row.price_on_weekends, Decimal("730"))

    def test_booking_serializer_hides_pending_images(self):
        property_obj, _, _ = self._create_verified_property_bundle()
        PropertyImage.objects.create(
            property=property_obj,
            image=self._image_file("public.jpg"),
            order=1,
            is_pending=False,
        )
        PropertyImage.objects.create(
            property=property_obj,
            image=self._image_file("pending.jpg"),
            order=2,
            is_pending=True,
        )

        request = APIRequestFactory().get("/api/property/properties/")
        data = PropertyBookingSerializer(
            property_obj,
            context={"request": request},
        ).data

        self.assertEqual(len(data["property_images"]), 1)
        first_image = data["property_images"][0]
        if isinstance(first_image, dict):
            self.assertIn("image_url", first_image)
        else:
            self.assertIn("media", str(first_image))

    def test_public_property_list_excludes_unverified(self):
        self._ensure_property_list_context()
        partner = self._create_partner()

        verified = self._create_property_for_list(partner, is_verified=True)
        unverified = self._create_property_for_list(partner, is_verified=False)

        request = APIRequestFactory().get("/api/property/properties/")
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = {str(item["guid"]) for item in response.data}
        self.assertIn(str(verified.guid), guids)
        self.assertNotIn(str(unverified.guid), guids)

    def test_partner_property_list_without_mine_includes_own_unverified(self):
        self._ensure_property_list_context()
        partner = self._create_partner()

        verified = self._create_property_for_list(partner, is_verified=True)
        unverified = self._create_property_for_list(partner, is_verified=False)

        request = APIRequestFactory().get("/api/property/properties/")
        force_authenticate(request, user=partner)
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = {str(item["guid"]) for item in response.data}
        self.assertIn(str(verified.guid), guids)
        self.assertIn(str(unverified.guid), guids)

    def test_partner_property_list_with_mine_includes_own_unverified_only(self):
        self._ensure_property_list_context()
        partner = self._create_partner()
        other_partner = self._create_partner()

        own_verified = self._create_property_for_list(partner, is_verified=True)
        own_unverified = self._create_property_for_list(partner, is_verified=False)
        other_verified = self._create_property_for_list(other_partner, is_verified=True)

        request = APIRequestFactory().get("/api/property/properties/?mine=1")
        force_authenticate(request, user=partner)
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = {str(item["guid"]) for item in response.data}
        self.assertIn(str(own_verified.guid), guids)
        self.assertIn(str(own_unverified.guid), guids)
        self.assertNotIn(str(other_verified.guid), guids)

    def test_partner_properties_sort_price_high_returns_descending(self):
        self._ensure_property_list_context()
        partner = self._create_partner()

        cheaper = self._create_property_for_list(partner, is_verified=True)
        expensive = self._create_property_for_list(partner, is_verified=True)

        from shared.date import month_start, month_end

        current_month_start = month_start(timezone.localdate())
        current_month_end = month_end(current_month_start)

        PropertyPrice.objects.update_or_create(
            property=cheaper,
            month_from=current_month_start,
            defaults={
                "month_to": current_month_end,
                "price_on_working_days": Decimal("1000000.00"),
                "price_on_weekends": Decimal("1000000.00"),
                "price_per_person": Decimal("0.00"),
            },
        )
        PropertyPrice.objects.update_or_create(
            property=expensive,
            month_from=current_month_start,
            defaults={
                "month_to": current_month_end,
                "price_on_working_days": Decimal("2000000.00"),
                "price_on_weekends": Decimal("2000000.00"),
                "price_per_person": Decimal("0.00"),
            },
        )

        request = APIRequestFactory().get("/api/property/partner/properties/?sort=price_high")
        force_authenticate(request, user=partner)
        response = PartnerPropertyListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = [str(item["guid"]) for item in response.data]
        self.assertGreaterEqual(len(guids), 2)
        self.assertEqual(guids[0], str(expensive.guid))
        self.assertEqual(guids[1], str(cheaper.guid))

    def test_partner_properties_endpoint_requires_partner_authentication(self):
        self._ensure_property_list_context()
        partner = self._create_partner()

        self._create_property_for_list(partner, is_verified=True)
        self._create_property_for_list(partner, is_verified=False)

        request = APIRequestFactory().get("/api/property/partner/properties/")
        response = PartnerPropertyListView.as_view()(request)

        self.assertEqual(response.status_code, 401)

    def test_partner_properties_endpoint_returns_only_authenticated_partner_properties(self):
        self._ensure_property_list_context()
        partner = self._create_partner()
        other_partner = self._create_partner()

        own_verified = self._create_property_for_list(partner, is_verified=True)
        own_unverified = self._create_property_for_list(partner, is_verified=False)
        other_verified = self._create_property_for_list(other_partner, is_verified=True)

        request = APIRequestFactory().get("/api/property/partner/properties/")
        force_authenticate(request, user=partner)
        response = PartnerPropertyListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = {str(item["guid"]) for item in response.data}
        self.assertIn(str(own_verified.guid), guids)
        self.assertIn(str(own_unverified.guid), guids)
        self.assertNotIn(str(other_verified.guid), guids)

    def test_partner_properties_sort_price_high_puts_null_prices_last(self):
        self._ensure_property_list_context()
        partner = self._create_partner()

        expensive = self._create_property_for_list(partner, is_verified=True)
        cheaper = self._create_property_for_list(partner, is_verified=True)
        no_price = self._create_property_for_list(partner, is_verified=True)

        from shared.date import month_start, month_end

        current_month_start = month_start(timezone.localdate())
        current_month_end = month_end(current_month_start)

        PropertyPrice.objects.update_or_create(
            property=expensive,
            month_from=current_month_start,
            defaults={
                "month_to": current_month_end,
                "price_on_working_days": Decimal("2000000.00"),
                "price_on_weekends": Decimal("2000000.00"),
                "price_per_person": Decimal("0.00"),
            },
        )
        PropertyPrice.objects.update_or_create(
            property=cheaper,
            month_from=current_month_start,
            defaults={
                "month_to": current_month_end,
                "price_on_working_days": Decimal("1000000.00"),
                "price_on_weekends": Decimal("1000000.00"),
                "price_per_person": Decimal("0.00"),
            },
        )
        PropertyPrice.objects.filter(property=no_price).delete()

        request = APIRequestFactory().get("/api/property/partner/properties/?sort=price_high")
        force_authenticate(request, user=partner)
        response = PartnerPropertyListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = [str(item["guid"]) for item in response.data]
        self.assertEqual(guids[0], str(expensive.guid))
        self.assertEqual(guids[1], str(cheaper.guid))
        self.assertEqual(guids[-1], str(no_price.guid))

    def test_partner_properties_sort_uses_current_month_cottage_price(self):
        self._ensure_property_list_context()
        partner = self._create_partner()
        today = timezone.localdate()

        from shared.date import month_start, month_end

        current_month_start = month_start(today)
        previous_month_start = month_start(current_month_start - timedelta(days=1))

        cottage_type = PropertyType.objects.filter(title_en="Cottages").first()
        self.assertIsNotNone(cottage_type)

        def make_cottage(title):
            location = PropertyLocation.objects.create(
                latitude="41.2995",
                longitude="69.2401",
                city="Tashkent",
                country="Uzbekistan",
            )
            prop = Property.objects.create(
                title=title,
                currency="UZS",
                property_type=cottage_type,
                property_location=location,
                partner=partner,
                verification_status=VerificationStatus.ACCEPTED,
            )
            PropertyRoom.objects.create(
                property=prop,
                guests=10,
                rooms=3,
                beds=3,
                bathrooms=2,
            )
            return prop

        cottage_a = make_cottage("Cottage A")
        cottage_b = make_cottage("Cottage B")

        # A: old month is cheaper, current month is more expensive.
        PropertyPrice.objects.create(
            property=cottage_a,
            month_from=previous_month_start,
            month_to=month_end(previous_month_start),
            price_on_working_days=Decimal("1000000"),
            price_on_weekends=Decimal("1000000"),
            price_per_person=Decimal("100000"),
        )
        PropertyPrice.objects.create(
            property=cottage_a,
            month_from=current_month_start,
            month_to=month_end(current_month_start),
            price_on_working_days=Decimal("2000000"),
            price_on_weekends=Decimal("2000000"),
            price_per_person=Decimal("100000"),
        )

        # B: old month is expensive, current month is less expensive.
        PropertyPrice.objects.create(
            property=cottage_b,
            month_from=previous_month_start,
            month_to=month_end(previous_month_start),
            price_on_working_days=Decimal("1500000"),
            price_on_weekends=Decimal("1500000"),
            price_per_person=Decimal("100000"),
        )
        PropertyPrice.objects.create(
            property=cottage_b,
            month_from=current_month_start,
            month_to=month_end(current_month_start),
            price_on_working_days=Decimal("1800000"),
            price_on_weekends=Decimal("1800000"),
            price_per_person=Decimal("100000"),
        )

        request = APIRequestFactory().get("/api/property/partner/properties/?sort=price_high")
        force_authenticate(request, user=partner)
        response = PartnerPropertyListView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = [str(item["guid"]) for item in response.data]
        self.assertLess(guids.index(str(cottage_a.guid)), guids.index(str(cottage_b.guid)))

    def test_property_list_filters_by_id_alias_with_district_guid(self):
        self._ensure_property_list_context()
        partner = self._create_partner()

        region = Region.objects.create(
            title_en=f"Region {uuid.uuid4().hex[:6]}",
            title_ru="Region ru",
            title_uz="Region uz",
        )
        district = District.objects.create(
            region=region,
            title_en=f"District {uuid.uuid4().hex[:6]}",
            title_ru="District ru",
            title_uz=f"District uz {uuid.uuid4().hex[:4]}",
        )

        matching = self._create_property_for_list(
            partner,
            is_verified=True,
            region=region,
            district=district,
        )
        non_matching = self._create_property_for_list(
            partner,
            is_verified=True,
        )

        request = APIRequestFactory().get(f"/api/property/properties/?id={district.guid}")
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = {str(item["guid"]) for item in response.data}
        self.assertIn(str(matching.guid), guids)
        self.assertNotIn(str(non_matching.guid), guids)

    def test_property_list_with_null_id_returns_empty(self):
        self._ensure_property_list_context()
        partner = self._create_partner()
        self._create_property_for_list(partner, is_verified=True)

        request = APIRequestFactory().get("/api/property/properties/?id=null")
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_property_list_with_null_region_id_returns_empty(self):
        self._ensure_property_list_context()
        partner = self._create_partner()
        self._create_property_for_list(partner, is_verified=True)

        request = APIRequestFactory().get("/api/property/properties/?region_id=null")
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_property_list_filters_by_region_id_alias(self):
        self._ensure_property_list_context()
        partner = self._create_partner()

        region = Region.objects.create(
            title_en=f"Region {uuid.uuid4().hex[:6]}",
            title_ru="Region ru",
            title_uz=f"Region uz {uuid.uuid4().hex[:4]}",
        )
        other_region = Region.objects.create(
            title_en=f"Region {uuid.uuid4().hex[:6]}",
            title_ru="Region ru",
            title_uz=f"Region uz {uuid.uuid4().hex[:4]}",
        )

        matching = self._create_property_for_list(
            partner,
            is_verified=True,
            region=region,
        )
        non_matching = self._create_property_for_list(
            partner,
            is_verified=True,
            region=other_region,
        )

        request = APIRequestFactory().get(f"/api/property/properties/?region_id={region.guid}")
        response = PropertyListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        guids = {str(item["guid"]) for item in response.data}
        self.assertIn(str(matching.guid), guids)
        self.assertNotIn(str(non_matching.guid), guids)


# ──────────────────────────────────────────────
# Model tests
# ──────────────────────────────────────────────


@override_settings(MEDIA_ROOT="/tmp/weel-test-media")
class PropertyModelTests(TestCase):
    def _svg_file(self, name="icon.svg"):
        return SimpleUploadedFile(
            name,
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )

    def test_property_type_str(self):
        pt = PropertyType.objects.create(
            title_en="Apartment",
            title_ru="Квартира",
            title_uz="Kvartira",
            icon=self._svg_file(),
        )
        self.assertEqual(str(pt), "Apartment")

    def test_property_location_str(self):
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.2995"),
            longitude=Decimal("69.2401"),
            city="Tashkent",
            country="Uzbekistan",
        )
        self.assertIn("Tashkent", str(loc))
        self.assertIn("Uzbekistan", str(loc))

    def test_property_save_sets_is_verified_from_status(self):
        partner = Partner.objects.create(
            first_name="P",
            last_name="P",
            username="partner_pt",
            phone_number="+998901111111",
            is_active=True,
        )
        pt = PropertyType.objects.create(
            title_en="Apt", title_ru="Кв", title_uz="Kv", icon=self._svg_file()
        )
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.3"), longitude=Decimal("69.2"),
            city="Tashkent", country="UZ",
        )
        prop = Property.objects.create(
            title="Test Property",
            property_type=pt,
            property_location=loc,
            partner=partner,
            verification_status=VerificationStatus.ACCEPTED,
        )
        self.assertTrue(prop.is_verified)

    def test_property_archive(self):
        partner = Partner.objects.create(
            first_name="P",
            last_name="P",
            username="partner_arch",
            phone_number="+998902222222",
            is_active=True,
        )
        pt = PropertyType.objects.create(
            title_en="Apt", title_ru="Кв", title_uz="Kv", icon=self._svg_file()
        )
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.3"), longitude=Decimal("69.2"),
            city="Tashkent", country="UZ",
        )
        prop = Property.objects.create(
            title="To Archive",
            property_type=pt,
            property_location=loc,
            partner=partner,
        )
        prop.archive()
        prop.refresh_from_db()
        self.assertTrue(prop.is_archived)

    def test_property_manager_excludes_archived(self):
        partner = Partner.objects.create(
            first_name="P",
            last_name="P",
            username="partner_mgr",
            phone_number="+998903333333",
            is_active=True,
        )
        pt = PropertyType.objects.create(
            title_en="Apt", title_ru="Кв", title_uz="Kv", icon=self._svg_file()
        )
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.3"), longitude=Decimal("69.2"),
            city="Tashkent", country="UZ",
        )
        prop = Property.objects.create(
            title="Archived Prop",
            property_type=pt,
            property_location=loc,
            partner=partner,
            is_archived=True,
        )
        self.assertNotIn(prop, Property.objects.all())


@override_settings(MEDIA_ROOT="/tmp/weel-test-media")
class PropertyReviewCommentCountSignalTests(TestCase):
    def _svg_file(self, name="icon.svg"):
        return SimpleUploadedFile(
            name,
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )

    def test_create_review_increments_comment_count(self):
        partner = Partner.objects.create(
            first_name="P", last_name="P", username="preview", phone_number="+998904444444", is_active=True
        )
        client = Client.objects.create(
            first_name="C", last_name="C", phone_number="+998905555555", is_active=True
        )
        pt = PropertyType.objects.create(
            title_en="Apt", title_ru="Кв", title_uz="Kv", icon=self._svg_file()
        )
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.3"), longitude=Decimal("69.2"), city="Tashkent", country="UZ"
        )
        prop = Property.objects.create(
            title="Review Prop",
            property_type=pt,
            property_location=loc,
            partner=partner,
        )
        self.assertEqual(prop.comment_count, 0)
        PropertyReview.objects.create(
            client=client,
            property=prop,
            rating=Decimal("5.0"),
        )
        prop.refresh_from_db()
        self.assertEqual(prop.comment_count, 1)


# ──────────────────────────────────────────────
# Filter unit tests
# ──────────────────────────────────────────────


@override_settings(MEDIA_ROOT="/tmp/weel-test-media")
class PropertyFilterUnitTests(TestCase):
    def _svg_file(self, name="icon.svg"):
        return SimpleUploadedFile(
            name,
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )

    def test_filter_property_services_valid_uuids(self):
        pt = PropertyType.objects.create(
            title_en="Apt", title_ru="Кв", title_uz="Kv", icon=self._svg_file()
        )
        svc1 = PropertyService.objects.create(
            title_en="WiFi", title_ru="WiFi", title_uz="WiFi",
            property_type=pt, icon=self._svg_file("w.svg"),
        )
        partner = Partner.objects.create(
            first_name="P", last_name="P", username="pfilt", phone_number="+998906666666", is_active=True
        )
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.3"), longitude=Decimal("69.2"), city="Tashkent", country="UZ"
        )
        prop = Property.objects.create(
            title="Filter Prop",
            property_type=pt,
            property_location=loc,
            partner=partner,
        )
        prop.property_services.add(svc1)
        qs = Property.objects.filter(guid=prop.guid)
        f = PropertyFilter(
            data={"property_services": str(svc1.guid)},
            queryset=qs,
        )
        self.assertTrue(f.qs.exists())

    def test_filter_guests_filters_by_total_guests(self):
        partner = Partner.objects.create(
            first_name="P", last_name="P", username="pguest", phone_number="+998907777777", is_active=True
        )
        pt = PropertyType.objects.create(
            title_en="Apt", title_ru="Кв", title_uz="Kv", icon=self._svg_file()
        )
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.3"), longitude=Decimal("69.2"), city="Tashkent", country="UZ"
        )
        prop = Property.objects.create(
            title="Guest Prop",
            property_type=pt,
            property_location=loc,
            partner=partner,
        )
        PropertyRoom.objects.create(property=prop, guests=4, rooms=2, beds=2, bathrooms=1)
        qs = Property.objects.filter(guid=prop.guid)
        f = PropertyFilter(data={"adults": 2, "children": 1}, queryset=qs)
        self.assertTrue(f.qs.exists())
        f2 = PropertyFilter(data={"adults": 5, "children": 0}, queryset=qs)
        self.assertFalse(f2.qs.exists())

    def test_filter_corporate_filters_by_property_detail_flag(self):
        partner = Partner.objects.create(
            first_name="P", last_name="P", username="pcorp", phone_number="+998908888888", is_active=True
        )
        pt = PropertyType.objects.create(
            title_en="Apt", title_ru="Кв", title_uz="Kv", icon=self._svg_file()
        )
        location = PropertyLocation.objects.create(
            latitude=Decimal("41.3"), longitude=Decimal("69.2"), city="Tashkent", country="UZ"
        )
        second_location = PropertyLocation.objects.create(
            latitude=Decimal("41.31"), longitude=Decimal("69.21"), city="Tashkent", country="UZ"
        )

        corporate_allowed = Property.objects.create(
            title="Corporate Allowed",
            property_type=pt,
            property_location=location,
            partner=partner,
        )
        corporate_not_allowed = Property.objects.create(
            title="Corporate Not Allowed",
            property_type=pt,
            property_location=second_location,
            partner=partner,
        )

        PropertyDetail.objects.create(
            property=corporate_allowed,
            description_en="desc",
            description_ru="desc",
            description_uz="desc",
            is_allowed_corporate=True,
        )
        PropertyDetail.objects.create(
            property=corporate_not_allowed,
            description_en="desc",
            description_ru="desc",
            description_uz="desc",
            is_allowed_corporate=False,
        )

        qs = Property.objects.filter(guid__in=[corporate_allowed.guid, corporate_not_allowed.guid])

        filtered_by_corporate = PropertyFilter(data={"corporate": "true"}, queryset=qs).qs
        self.assertEqual(
            {str(item.guid) for item in filtered_by_corporate},
            {str(corporate_allowed.guid)},
        )

        filtered_by_alias = PropertyFilter(data={"is_allowed_corporate": "1"}, queryset=qs).qs
        self.assertEqual(
            {str(item.guid) for item in filtered_by_alias},
            {str(corporate_allowed.guid)},
        )

    def test_filter_property_services_invalid_uuid_returns_none(self):
        qs = Property.objects.all()
        f = PropertyFilter(data={"property_services": "not-a-uuid"}, queryset=qs)
        self.assertEqual(f.qs.count(), 0)


# ──────────────────────────────────────────────
# Serializer tests
# ──────────────────────────────────────────────


class PropertyTypeSlugRelatedFieldTests(TestCase):
    def test_template_id_raises_validation_error(self):
        field = PropertyTypeSlugRelatedField(
            slug_field="guid",
            queryset=PropertyType.objects.none(),
        )
        with self.assertRaises(Exception):
            field.to_internal_value("{{id}}")
        with self.assertRaises(Exception):
            field.to_internal_value("{{ property_type_id }}")


# ──────────────────────────────────────────────
# API view tests
# ──────────────────────────────────────────────


@override_settings(MEDIA_ROOT="/tmp/weel-test-media")
class PropertyAPITests(TestCase):
    def _svg_file(self, name="icon.svg"):
        return SimpleUploadedFile(
            name,
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )

    def setUp(self):
        self.client = APIClient()

    def test_types_list_returns_200(self):
        response = self.client.get("/api/property/types/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_services_list_returns_200(self):
        response = self.client.get("/api/property/services/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_properties_list_unauthenticated_returns_200(self):
        ExchangeRate.objects.create(currency="USD", rate=Decimal("12000"))
        response = self.client.get("/api/property/properties/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_apartment_list_returns_property_price_list(self):
        ExchangeRate.objects.create(currency="USD", rate=Decimal("12000"))
        partner = Partner.objects.create(
            first_name="P",
            last_name="P",
            username=f"partner_{uuid.uuid4().hex[:8]}",
            phone_number=f"+99890{uuid.uuid4().int % 10**7:07d}",
            is_active=True,
        )
        pt = PropertyType.objects.create(
            title_en="Apartment",
            title_ru="Кв",
            title_uz="Kv",
            icon=self._svg_file("apt.svg"),
        )
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.3"),
            longitude=Decimal("69.2"),
            city="Tashkent",
            country="UZ",
        )
        prop = Property.objects.create(
            title=f"Ordering Apt {uuid.uuid4().hex[:6]}",
            property_type=pt,
            property_location=loc,
            partner=partner,
            currency="UZS",
            verification_status=VerificationStatus.ACCEPTED,
        )
        PropertyRoom.objects.create(property=prop, guests=2, rooms=1, beds=1, bathrooms=1)

        from shared.date import month_start, month_end

        today = timezone.localdate()
        current_month_start = month_start(today)
        next_month_start = month_start(current_month_start + timedelta(days=32))

        PropertyPrice.objects.create(
            property=prop,
            month_from=current_month_start,
            month_to=month_end(current_month_start),
            price_on_working_days=Decimal("100"),
            price_on_weekends=Decimal("100"),
            price_per_person=Decimal("0"),
        )
        PropertyPrice.objects.create(
            property=prop,
            month_from=next_month_start,
            month_to=month_end(next_month_start),
            price_on_working_days=Decimal("150"),
            price_on_weekends=Decimal("150"),
            price_per_person=Decimal("0"),
        )

        response = self.client.get(
            "/api/property/properties/apartments/",
            {"search": prop.title},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        prices = response.data[0]["price"]
        self.assertIsInstance(prices, list)
        self.assertGreaterEqual(len(prices), 2)
        self.assertIn("price_per_person", prices[0])
        self.assertIn("price_on_working_days", prices[0])
        self.assertIn("price_on_weekends", prices[0])

    def test_property_create_unauthenticated_returns_401_or_403(self):
        response = self.client.post(
            "/api/property/properties/",
            data={"title": "New"},
            format="json",
        )
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_property_retrieve_returns_200_for_verified_property(self):
        ExchangeRate.objects.create(currency="USD", rate=Decimal("12000"))
        partner = Partner.objects.create(
            first_name="P", last_name="P", username="papi", phone_number="+998908888888", is_active=True
        )
        pt = PropertyType.objects.create(
            title_en="Apt", title_ru="Кв", title_uz="Kv", icon=self._svg_file()
        )
        loc = PropertyLocation.objects.create(
            latitude=Decimal("41.3"), longitude=Decimal("69.2"), city="Tashkent", country="UZ"
        )
        prop = Property.objects.create(
            title="API Prop",
            price=Decimal("100"),
            property_type=pt,
            property_location=loc,
            partner=partner,
            verification_status=VerificationStatus.ACCEPTED,
        )
        PropertyDetail.objects.create(
            property=prop,
            description_en="Desc",
            description_ru="Описание",
            description_uz="Tavsif",
            check_in=time(14, 0),
            check_out=time(12, 0),
        )
        PropertyRoom.objects.create(property=prop, guests=2, rooms=1, beds=1, bathrooms=1)
        from shared.date import month_start, month_end
        current_month_start = month_start(timezone.localdate())
        PropertyPrice.objects.create(
            property=prop,
            month_from=current_month_start,
            month_to=month_end(current_month_start),
            price_on_working_days=Decimal("100"),
            price_on_weekends=Decimal("100"),
            price_per_person=Decimal("0"),
        )
        response = self.client.get(
            f"/api/property/properties/{prop.guid}/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "API Prop")
        self.assertIsInstance(response.data["price"], list)
        self.assertGreaterEqual(len(response.data["price"]), 1)
        self.assertIn("price_per_person", response.data["price"][0])
        self.assertEqual(
            response.data["price"][0]["price_on_working_days"],
            Decimal("1200000"),
        )

    def test_property_retrieve_returns_404_for_nonexistent(self):
        response = self.client.get(
            "/api/property/properties/00000000-0000-0000-0000-000000000000/"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
