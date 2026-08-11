from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, complete_onboarding, create_business_for_owner
from apps.inventory.models import InventoryLot, Product
from apps.pricing.services import ensure_default_tiers, set_lot_prices


class Command(BaseCommand):
    help = "Seed fictional Persian demo data for local development (SANGA)."

    @transaction.atomic
    def handle(self, *args, **options):
        ensure_default_tiers()

        owner, _ = User.objects.get_or_create(
            phone="09121111111",
            defaults={"full_name": "مالک دمو (فرضی)"},
        )
        if not owner.full_name:
            owner.full_name = "مالک دمو (فرضی)"
            owner.save(update_fields=["full_name"])

        membership = BusinessMembership.objects.filter(user=owner, role=BusinessMembership.Role.OWNER).first()
        if membership:
            business = membership.business
            warehouse = business.warehouses.filter(is_default=True).first() or business.warehouses.first()
            if warehouse is None:
                warehouse = add_warehouse(business=business, name="انبار مرکزی", city="محلات", is_default=True)
        else:
            business = create_business_for_owner(
                owner=owner,
                name="سنگبری آذرخش (دمو ـ فرضی)",
                city="محلات",
                province="مرکزی",
                phone="09121111111",
            )
            warehouse = add_warehouse(business=business, name="انبار مرکزی", city="محلات", is_default=True)
            complete_onboarding(business)
            membership = BusinessMembership.objects.get(user=owner, business=business)

        samples = [
            ("تراورتن عباس‌آباد (فرضی)", "تراورتن", "کرم", "محلات", "120.000", "1850000", "2600000"),
            ("مرمریت لاشتر روشن (فرضی)", "مرمریت", "کرم روشن", "اصفهان", "80.500", "2100000", "2950000"),
            ("چینی الیگودرز (فرضی)", "چینی", "سفید", "الیگودرز", "45.000", "3200000", "4200000"),
        ]
        for idx, (name, stone, color, region, qty, b2b, b2c) in enumerate(samples, start=1):
            product, _ = Product.objects.get_or_create(
                business=business,
                commercial_name=name,
                defaults={
                    "stone_type": stone,
                    "primary_color": color,
                    "quarry_region": region,
                    "description_public": "نمونه فرضی توسعه سنگا — داده واقعی نیست.",
                },
            )
            code = f"DEMO-{idx:03d}"
            lot, created = InventoryLot.objects.get_or_create(
                business=business,
                lot_code=code,
                defaults={
                    "product": product,
                    "warehouse": warehouse,
                    "status": InventoryLot.Status.AVAILABLE,
                    "visibility": InventoryLot.Visibility.CUSTOMER_CATALOG,
                    "available_sqm": Decimal(qty),
                    "original_sqm": Decimal(qty),
                    "grade": "ممتاز",
                    "processing_type": "صیقلی",
                    "inventory_confirmed_at": timezone.now(),
                    "description": "داده دمو فرضی برای تست سنگا",
                },
            )
            if created or not lot.prices.exists():
                set_lot_prices(
                    lot=lot,
                    b2b_amount=Decimal(b2b),
                    b2c_amount=Decimal(b2c),
                    currency="IRR",
                )

        self.stdout.write(self.style.SUCCESS("Demo seed complete (fictional SANGA data)."))
        self.stdout.write(f"Login phone: {owner.phone}")
        self.stdout.write("Business id: " + str(business.id))
