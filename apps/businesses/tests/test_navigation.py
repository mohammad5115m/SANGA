"""Navigation and terminology.

Two things worth pinning: the removed features are actually gone from the
interface, and «محموله» never reaches a user's screen.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.businesses.models import BusinessMembership
from apps.core.testing import make_business, make_item, make_user, owner_membership
from apps.pricing.services import ensure_default_tiers


@pytest.fixture
def shop(db):
    ensure_default_tiers()
    business = make_business(name="سنگ ناوبری", owner_phone="09241110001")
    business.seat_limit = 5
    business.save(update_fields=["seat_limit"])
    make_item(business, lot_code="NAV-1", b2b="1000000", b2c="1500000")
    return business


def _login(client, business, membership=None):
    user = (membership or owner_membership(business)).user
    client.force_login(user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


PRIMARY_NAV = ("خانه", "موجودی من", "بازار", "فاکتورها", "کاتالوگ‌ها", "بیشتر")


@pytest.mark.parametrize("permissions", [[], ["ledger.view"], ["report.view"]])
def test_navigation_matches_restricted_member_capabilities(client, shop, permissions):
    staff = BusinessMembership.objects.create(
        business=shop, user=make_user("09995550201"), role="staff", permissions=permissions,
    )
    _login(client, shop, staff)
    body = client.get(reverse("businesses:more")).content.decode()
    assert 'href="/app/inventory/"' not in body
    assert ('href="/app/reports/"' in body) == ("report.view" in permissions)


@pytest.mark.django_db
def test_the_primary_navigation_has_the_six_destinations(client, shop):
    _login(client, shop)
    body = client.get(reverse("businesses:dashboard")).content.decode()
    for label in PRIMARY_NAV:
        assert label in body, label


@pytest.mark.django_db
def test_the_removed_features_are_gone_from_the_navigation(client, shop):
    _login(client, shop)
    body = client.get(reverse("businesses:dashboard")).content.decode()
    assert "تابلوی تقاضا" not in body
    assert "مخاطبین" not in body
    assert "/app/contacts/" not in body
    assert "انبار" not in body


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name",
    [
        "businesses:dashboard",
        "businesses:more",
        "inventory:lot_list",
        "marketplace:home",
        "trading:received_list",
        "catalog_manage:list",
        "businesses:colleagues",
        "accounting:index",
        "invoicing:list",
        "inquiries:inbox",
        "inquiries:leads",
        "reporting:index",
    ],
)
def test_no_page_says_mahmoule(client, shop, url_name):
    """«محموله» is shipping vocabulary and confused sellers. It is «محصول» now."""
    _login(client, shop)
    body = client.get(reverse(url_name)).content.decode()
    assert "محموله" not in body


@pytest.mark.django_db
def test_the_more_hub_links_to_the_secondary_screens(client, shop):
    _login(client, shop)
    body = client.get(reverse("businesses:more")).content.decode()
    for label in ("لیست همکاران", "دفتر حساب", "فاکتورها", "درخواست‌های خرید", "مشتریان", "گزارش‌ها", "تیم", "تنظیمات"):
        assert label in body, label


@pytest.mark.django_db
def test_the_hub_hides_what_the_member_cannot_reach(client, shop):
    """Navigation follows capabilities, so a link never ends in «دسترسی ندارید»."""
    viewer = BusinessMembership.objects.create(
        user=make_user("09241119999"),
        business=shop,
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    _login(client, shop, viewer)
    body = client.get(reverse("businesses:more")).content.decode()

    assert "لیست همکاران" in body
    assert "دفتر حساب" not in body
    assert "گزارش‌ها" not in body
    assert "تیم" not in body


@pytest.mark.django_db
def test_dashboard_does_not_offer_product_creation_to_a_viewer(client, shop):
    viewer = BusinessMembership.objects.create(
        user=make_user("09241118888"),
        business=shop,
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    _login(client, shop, viewer)

    body = client.get(reverse("businesses:dashboard")).content.decode()

    assert "افزودن محصول" not in body
    assert reverse("inventory:quick_add_start") not in body
    assert "برای ثبت محصول با مدیر کسب‌وکار هماهنگ کنید" not in body, "shop already has active inventory"


@pytest.mark.django_db
def test_the_dashboard_leads_with_what_needs_doing(client, shop):
    _login(client, shop)
    body = client.get(reverse("businesses:dashboard")).content.decode()
    assert "کارهای امروز" in body
    assert "افزودن محصول" in body
    # Operational, not analytical: no charting library, no canvas.
    assert "<canvas" not in body
    assert "chart.js" not in body.lower()
    assert "<svg" in body, "icons are fine; plotted graphics are what we avoid"
