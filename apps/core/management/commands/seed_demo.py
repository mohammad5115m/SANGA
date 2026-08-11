from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.businesses.models import BusinessMembership
from apps.businesses.services import add_warehouse, create_business_for_owner
from apps.pricing.models import PriceTier


class Command(BaseCommand):
    help = "Seed fictional Persian demo data for local development."

    @transaction.atomic
    def handle(self, *args, **options):
        PriceTier.objects.get_or_create(code="b2b", defaults={"name": "قیمت همکار", "sort_order": 1})
        PriceTier.objects.get_or_create(code="b2c", defaults={"name": "قیمت مشتری", "sort_order": 2})

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
        else:
            business = create_business_for_owner(
                owner=owner,
                name="سنگبری آذرخش (دمو ـ فرضی)",
                city="محلات",
                province="مرکزی",
                phone="09121111111",
            )
            add_warehouse(business=business, name="انبار مرکزی", city="محلات", is_default=True)

        self.stdout.write(self.style.SUCCESS("Demo seed complete (fictional data)."))
        self.stdout.write(f"Login phone: {owner.phone}")
        self.stdout.write(f"Business: {business.name}")
