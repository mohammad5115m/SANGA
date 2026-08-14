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
from apps.inventory.models import Application, InventoryLot, Product, VocabularyTerm
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
    prefixes = {
        "تراورتن": "T", "مرمریت": "M", "گرانیت": "G", "کریستال": "C",
        "مرمر": "O", "لایمستون": "L", "ترامیت": "TR", "چینی": "CH",
    }
    stone, _created = VocabularyTerm.objects.get_or_create(
        kind=VocabularyTerm.Kind.STONE_TYPE,
        name=stone_type,
        defaults={"code_prefix": prefixes.get(stone_type, ""), "is_active": True},
    )
    suffix = commercial_name.removeprefix("سنگ ").removeprefix(stone_type).strip()
    suffix = kwargs.pop("name_suffix", suffix)
    product = Product.objects.create(
        business=business,
        stone=stone,
        name_suffix=suffix,
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
    stock_mode: str = "exact",
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
    elif InventoryLot.objects.filter(product=product).exists():
        original = product
        product = Product.objects.create(
            business=business,
            stone=original.stone,
            name_suffix=original.name_suffix,
            pattern=original.pattern,
            description_public=original.description_public,
            description_professional=original.description_professional,
        )
        product.applications.set(original.applications.all())
    if not lot_code:
        lot_code = f"IT-{InventoryLot.objects.count() + 1:06d}"

    for retired in (
        "grade",
        "original_sqm",
        "slab_count",
        "bundle_count",
        "location_province",
        "location_city",
        "location_address",
        "warehouse",
    ):
        kwargs.pop(retired, None)
    quantity = None if stock_mode != "exact" or available_sqm is None else Decimal(str(available_sqm))

    item = InventoryLot.objects.create(
        business=business,
        product=product,
        lot_code=lot_code,
        status=status,
        is_visible=is_visible,
        availability_status=availability_status,
        available_sqm=quantity,
        stock_confirmed_at=(
            stock_confirmed_at if stock_confirmed_at is not None else (timezone.now() if quantity is not None else None)
        ),
        stock_valid_for_days=stock_valid_for_days,
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
