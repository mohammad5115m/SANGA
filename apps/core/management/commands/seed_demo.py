from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import BusinessMembership
from apps.businesses.services import complete_onboarding, create_business_for_owner
from apps.inventory.models import Application, InventoryLot, Product, VocabularyTerm
from apps.pricing.services import ensure_default_tiers, set_lot_price

#: name, stone, colour, quarry, sqm, b2b, b2c, application codes
SAMPLES = [
    ("تراورتن عباس‌آباد (فرضی)", "تراورتن", "کرم", "عباس‌آباد", "120.000", "1850000", "2600000",
     ["exterior-facade", "floor"]),
    ("مرمریت لاشتر روشن (فرضی)", "مرمریت", "کرم روشن", "اصفهان", "80.500", "2100000", "2950000",
     ["interior-wall", "counter"]),
    ("چینی الیگودرز (فرضی)", "چینی", "سفید", "الیگودرز", "45.000", "3200000", "4200000",
     ["bathroom", "floor"]),
]


class Command(BaseCommand):
    help = "Seed fictional Persian demo data for local development (SANGA)."

    @transaction.atomic
    def handle(self, *args, **options):
        ensure_default_tiers()

        owner, _ = User.objects.get_or_create(
            phone="09121111111",
            defaults={"full_name": "مالک دمو (فرضی)"},
        )

        membership = BusinessMembership.objects.filter(user=owner, role=BusinessMembership.Role.OWNER).first()
        if membership:
            business = membership.business
        else:
            business = create_business_for_owner(
                owner=owner,
                name="سنگبری آذرخش (دمو ـ فرضی)",
                city="محلات",
                province="مرکزی",
                phone="09121111111",
            )
            complete_onboarding(business)

        prefixes = {
            "تراورتن": "T", "مرمریت": "M", "گرانیت": "G", "کریستال": "C",
            "مرمر": "O", "لایمستون": "L", "ترامیت": "TR", "چینی": "CH",
        }
        for idx, (name, stone_name, _color, _region, qty, b2b, b2c, apps) in enumerate(
            SAMPLES, start=1
        ):
            stone, _ = VocabularyTerm.objects.get_or_create(
                kind=VocabularyTerm.Kind.STONE_TYPE,
                name=stone_name,
                defaults={"code_prefix": prefixes[stone_name], "is_active": True},
            )
            code = f"DEMO-{idx:03d}"
            item = InventoryLot.objects.filter(lot_code=code, business=business).first()
            created = item is None
            if item is None:
                product = Product.objects.create(
                    business=business,
                    stone=stone,
                    name_suffix=name.removeprefix("سنگ ").removeprefix(stone_name).strip(),
                    description_public="نمونه فرضی توسعه سنگا — داده واقعی نیست.",
                )
                product.applications.set(Application.objects.filter(code__in=apps))
                item = InventoryLot.objects.create(
                    business=business,
                    lot_code=code,
                    product=product,
                    status=InventoryLot.Status.ACTIVE,
                    is_visible=True,
                    availability_status=InventoryLot.Availability.AVAILABLE,
                    available_sqm=Decimal(qty),
                    stock_confirmed_at=timezone.now(),
                    processing_type="صیقلی",
                    description="داده دمو فرضی برای تست سنگا",
                )
            if created or not item.prices.exists():
                set_lot_price(lot=item, tier_code="b2b", amount=Decimal(b2b))
                set_lot_price(lot=item, tier_code="b2c", amount=Decimal(b2c))

        # One item left unpublished so the «موجودی من» / «بازار» split is visible
        # locally, and one on inquiry stock so the freshness wording shows up.
        hidden = InventoryLot.objects.filter(business=business, lot_code="DEMO-002").first()
        if hidden is not None and hidden.is_visible:
            hidden.is_visible = False
            hidden.save(update_fields=["is_visible", "updated_at"])

        inquiry_item = InventoryLot.objects.filter(business=business, lot_code="DEMO-003").first()
        if inquiry_item is not None:
            inquiry_item.available_sqm = None
            inquiry_item.stock_confirmed_at = None
            inquiry_item.save(update_fields=["available_sqm", "stock_confirmed_at", "stock_expires_at", "updated_at"])

        partner_owner, _ = User.objects.get_or_create(
            phone="09122222222",
            defaults={"full_name": "شریک دمو (فرضی)"},
        )
        if not BusinessMembership.objects.filter(user=partner_owner, role=BusinessMembership.Role.OWNER).exists():
            partner_business = create_business_for_owner(
                owner=partner_owner,
                name="بازرگانی سنگ پارس (دمو ـ فرضی)",
                city="تهران",
                province="تهران",
                phone="09122222222",
            )
            complete_onboarding(partner_business)

        self.stdout.write(self.style.SUCCESS("Demo seed complete (fictional SANGA data)."))
        self.stdout.write(f"Seller login phone: {owner.phone}")
        self.stdout.write(f"Colleague login phone: {partner_owner.phone}")
