from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import complete_onboarding, create_business_for_owner
from apps.catalog.models import StorefrontCollection
from apps.catalog.services import (
    apply_storefront_suggestions,
    ensure_default_storefront_collections,
)
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


def _restore_demo_business(*, phone: str, full_name: str, name: str, city: str, province: str):
    """Create or repair one login-ready fictional demo business."""
    user, _ = User.objects.update_or_create(
        phone=phone,
        defaults={"full_name": full_name, "is_active": True},
    )
    user_updates: list[str] = []
    if not user.is_active:
        user.is_active = True
        user_updates.append("is_active")
    if user.full_name != full_name:
        user.full_name = full_name
        user_updates.append("full_name")
    if user_updates:
        user.save(update_fields=user_updates)

    membership = BusinessMembership.objects.filter(
        user=user,
        role=BusinessMembership.Role.OWNER,
    ).first()
    if membership is None:
        business = create_business_for_owner(
            owner=user,
            name=name,
            city=city,
            province=province,
            phone=phone,
        )
        complete_onboarding(business)
        return user, business

    business = membership.business
    if membership.status != BusinessMembership.Status.ACTIVE:
        membership.status = BusinessMembership.Status.ACTIVE
        membership.save(update_fields=["status"])

    business.status = Business.Status.ACTIVE
    business.verification_status = Business.VerificationStatus.VERIFIED
    business.plan = Business.Plan.SELLER
    business.active_until = None
    business.save(
        update_fields=[
            "status",
            "verification_status",
            "plan",
            "active_until",
            "updated_at",
        ]
    )
    if not business.is_onboarded:
        complete_onboarding(business)
    return user, business


class Command(BaseCommand):
    help = "Seed fictional Persian demo data for local development (SANGA)."

    @transaction.atomic
    def handle(self, *args, **options):
        ensure_default_tiers()

        owner, business = _restore_demo_business(
            phone="09121111111",
            full_name="مالک دمو (فرضی)",
            name="سنگبری آذرخش (دمو ـ فرضی)",
            city="محلات",
            province="مرکزی",
        )

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

        # Keep the demo storefront visibly representative of the real customer
        # journey: one current promotion and one editable merchandising section.
        special_item = InventoryLot.objects.get(business=business, lot_code="DEMO-001")
        set_lot_price(
            lot=special_item,
            tier_code="b2c",
            amount=Decimal("2600000"),
            special_amount=Decimal("2190000"),
            special_until=timezone.now() + timedelta(days=2, hours=6),
            valid_for_days=7,
        )
        membership = BusinessMembership.objects.get(
            business=business,
            user=owner,
            role=BusinessMembership.Role.OWNER,
        )
        ensure_default_storefront_collections(business=business)
        economic = business.storefront_collections.filter(
            suggestion_kind=StorefrontCollection.SuggestionKind.ECONOMIC
        ).first()
        if economic is not None:
            apply_storefront_suggestions(collection=economic, membership=membership)
            if not economic.is_active:
                economic.is_active = True
                economic.save(update_fields=["is_active", "updated_at"])

        # One item is left unpublished so the «موجودی من» / «بازار» split is
        # visible locally. Another has no confirmed quantity and therefore stays
        # out of the transaction-ready colleague marketplace.
        hidden = InventoryLot.objects.filter(business=business, lot_code="DEMO-002").first()
        if hidden is not None and hidden.is_visible:
            hidden.is_visible = False
            hidden.save(update_fields=["is_visible", "updated_at"])

        inquiry_item = InventoryLot.objects.filter(business=business, lot_code="DEMO-003").first()
        if inquiry_item is not None:
            inquiry_item.available_sqm = None
            inquiry_item.stock_confirmed_at = None
            inquiry_item.save(update_fields=["available_sqm", "stock_confirmed_at", "stock_expires_at", "updated_at"])

        partner_owner, _partner_business = _restore_demo_business(
            phone="09122222222",
            full_name="شریک دمو (فرضی)",
            name="بازرگانی سنگ پارس (دمو ـ فرضی)",
            city="تهران",
            province="تهران",
        )

        self.stdout.write(self.style.SUCCESS("Demo seed complete (fictional SANGA data)."))
        self.stdout.write(f"Seller login phone: {owner.phone}")
        self.stdout.write(f"Colleague login phone: {partner_owner.phone}")
