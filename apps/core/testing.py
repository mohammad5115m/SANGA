"""Builders for tests.

Not a factory framework — just the four or five objects every SANGA test needs,
with defaults that produce a *sellable, publicly visible* item. Tests that care
about a lifecycle state set it explicitly, which keeps the interesting part of
each test visible instead of buried in twenty lines of setup.

Kept out of ``conftest.py`` so it can be imported from any app's tests.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import create_business_for_owner
from apps.inventory.models import Application, InventoryLot, Product
from apps.pricing.services import ensure_default_tiers, set_lot_price

User = get_user_model()


def make_user(phone: str, **kwargs) -> User:
    return User.objects.create_user(phone=phone, **kwargs)


def make_business(
    *,
    name: str,
    owner_phone: str,
    city: str = "اصفهان",
    province: str = "اصفهان",
    status: str = Business.Status.ACTIVE,
    **kwargs,
) -> Business:
    """Provision a Business the way a Platform Admin would."""
    owner = make_user(owner_phone, **kwargs)
    business = create_business_for_owner(owner=owner, name=name, city=city, province=province)
    if status != Business.Status.ACTIVE:
        business.status = status
        business.save(update_fields=["status"])
    return business


def owner_membership(business: Business) -> BusinessMembership:
    return BusinessMembership.objects.get(business=business, role=BusinessMembership.Role.OWNER)


def make_product(
    business: Business,
    *,
    commercial_name: str = "تراورتن کرم",
    stone_type: str = "تراورتن",
    primary_color: str = "کرم",
    quarry_region: str = "عباس‌آباد",
    applications: list[str] | None = None,
    **kwargs,
) -> Product:
    product = Product.objects.create(
        business=business,
        commercial_name=commercial_name,
        stone_type=stone_type,
        primary_color=primary_color,
        quarry_region=quarry_region,
        **kwargs,
    )
    if applications:
        product.applications.set(Application.objects.filter(code__in=applications))
    return product


def make_item(
    business: Business,
    *,
    product: Product | None = None,
    lot_code: str = "",
    is_visible: bool = True,
    availability_status: str = InventoryLot.Availability.AVAILABLE,
    stock_mode: str = InventoryLot.StockMode.EXACT,
    available_sqm: Decimal | str = "100",
    stock_valid_for_days: int = 7,
    stock_confirmed_at=None,
    status: str = InventoryLot.Status.ACTIVE,
    b2b: Decimal | str | None = None,
    b2c: Decimal | str | None = None,
    **kwargs,
) -> InventoryLot:
    """A visible, available, freshly-confirmed item unless told otherwise."""
    if product is None:
        product = make_product(business)
    if not lot_code:
        lot_code = f"IT-{InventoryLot.objects.filter(business=business).count() + 1:04d}"

    item = InventoryLot.objects.create(
        business=business,
        product=product,
        lot_code=lot_code,
        status=status,
        is_visible=is_visible,
        availability_status=availability_status,
        stock_mode=stock_mode,
        available_sqm=Decimal(str(available_sqm)),
        original_sqm=Decimal(str(available_sqm)),
        stock_confirmed_at=stock_confirmed_at if stock_confirmed_at is not None else timezone.now(),
        stock_valid_for_days=stock_valid_for_days,
        location_city=business.city,
        location_province=business.province,
        **kwargs,
    )
    if b2b is not None or b2c is not None:
        set_prices(item, b2b=b2b, b2c=b2c)
    return item


def set_prices(item: InventoryLot, *, b2b=None, b2c=None, valid_for_days: int = 7) -> None:
    ensure_default_tiers()
    if b2b is not None:
        set_lot_price(lot=item, tier_code="b2b", amount=Decimal(str(b2b)), valid_for_days=valid_for_days)
    if b2c is not None:
        set_lot_price(lot=item, tier_code="b2c", amount=Decimal(str(b2c)), valid_for_days=valid_for_days)


def expire_stock(item: InventoryLot) -> InventoryLot:
    """Push the confirmation far enough back that the quantity is no longer trusted."""
    item.stock_confirmed_at = timezone.now() - timedelta(days=item.stock_valid_for_days + 1)
    item.save()
    item.refresh_from_db()
    return item


def expire_price(item: InventoryLot, tier_code: str = "b2c") -> None:
    price = item.prices.select_related("tier").get(tier__code=tier_code)
    price.price_confirmed_at = timezone.now() - timedelta(days=price.price_valid_for_days + 1)
    price.save()
