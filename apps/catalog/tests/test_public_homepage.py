from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Business
from apps.catalog.models import PublicHomepage
from apps.core.testing import make_business, make_item, make_product, owner_membership
from apps.inventory.models import InventoryLot

pytestmark = pytest.mark.django_db


@pytest.fixture
def shops():
    selected = make_business(name="فروشندهٔ منتخب", owner_phone="09120008831")
    other = make_business(name="فروشندهٔ دارای لینک خصوصی", owner_phone="09120008832")
    stone = make_product(
        selected,
        commercial_name="تراورتن منتخب",
        description_public="توضیح قابل نمایش برای مشتری",
        description_colleague="یادداشت اختصاصی همکار",
    )
    featured = make_item(selected, product=stone, b2b="9182736", b2c="1526374")
    make_item(selected, product=make_product(selected, commercial_name="تراورتن پنهان"), is_visible=False)
    make_item(
        selected, product=make_product(selected, commercial_name="تراورتن پیش‌نویس"),
        status=InventoryLot.Status.DRAFT,
    )
    make_item(other, product=make_product(other, commercial_name="تراورتن لینک خصوصی"))
    return selected, other, featured


@pytest.fixture
def homepage():
    return PublicHomepage.objects.update_or_create(pk=PublicHomepage.KEY, defaults={"business": None})[0]


@pytest.fixture
def admin_user():
    return User.objects.create_superuser(phone="09120008833", password="Fixture-Only-Password-83!")


@pytest.mark.parametrize("route", ["core:home", "core:public_search"])
def test_home_without_selection_is_public_and_does_not_discover_sellers(client, shops, homepage, route):
    response = client.get(reverse(route))
    assert response.status_code == 200
    assert "ویترین عمومی سنگ اصفهان" in response.content.decode()
    assert "ورود به پنل" in response.content.decode()
    for business in shops[:2]:
        assert business.name not in response.content.decode()
        assert business.storefront_token not in response.content.decode()


@pytest.mark.parametrize("audience", ["anonymous", "owner", "admin"])
def test_all_visitors_see_the_same_selected_sellers_public_data(client, shops, homepage, admin_user, audience):
    seller, other, featured = shops
    homepage.business = seller
    homepage.save(update_fields=["business"])
    if audience != "anonymous":
        client.force_login(owner_membership(other).user if audience == "owner" else admin_user)

    response = client.get("/")
    assert response.status_code == 200
    assert [card["lot"].pk for card in response.context["cards"]] == [featured.pk]
    assert {p.tier.code for p in response.context["cards"][0]["lot"].prices.all()} == {"b2c"}
    body = response.content.decode()
    for confidential in ("یادداشت اختصاصی همکار", "تراورتن پنهان", "تراورتن پیش‌نویس", other.storefront_token):
        assert confidential not in body
    assert reverse("catalog:lot_detail", args=[seller.storefront_token, featured.pk]) in body
    assert reverse("accounts:login") in body


@pytest.mark.parametrize("change", ["suspended", "expired", "browse", "pending"])
def test_unavailable_selection_fails_closed(client, shops, homepage, change):
    seller, other, _ = shops
    homepage.business = seller
    homepage.save(update_fields=["business"])
    data = {
        "suspended": {"status": Business.Status.SUSPENDED},
        "expired": {"active_until": timezone.localdate() - timedelta(days=1)},
        "browse": {"plan": Business.Plan.BROWSE},
        "pending": {"verification_status": Business.VerificationStatus.PENDING},
    }[change]
    Business.objects.filter(pk=seller.pk).update(**data)
    response = client.get("/")
    assert response.status_code == 200
    body = response.content.decode()
    for business in (seller, other):
        assert business.storefront_token not in body
        assert business.name not in body


def test_search_alias_filters_the_selected_storefront_only(client, shops, homepage):
    homepage.business = shops[0]
    homepage.save(update_fields=["business"])
    assert [c["lot"].pk for c in client.get("/search/", {"q": "منتخب"}).context["cards"]] == [shops[2].pk]
    assert not client.get("/search/", {"q": "لینک خصوصی"}).context["cards"]


def test_admin_selection_is_explicit_clearable_and_eligible(client, shops, homepage, admin_user):
    target = reverse("admin:catalog_publichomepage_change", args=[homepage.pk])
    seller, other, _ = shops
    other.status = Business.Status.SUSPENDED
    other.save(update_fields=["status"])
    for visitor in (None, owner_membership(seller).user):
        if visitor:
            client.force_login(visitor)
        assert client.post(target, {"business": seller.pk, "_save": "1"}).status_code == 302
        homepage.refresh_from_db()
        assert homepage.business is None

    client.force_login(admin_user)
    assert target in client.get(reverse("admin:index")).content.decode()
    invalid = client.post(target, {"business": other.pk, "_save": "1"})
    assert invalid.status_code == 200
    assert invalid.context["adminform"].form.errors["business"]
    assert client.post(target, {"business": seller.pk, "_save": "1"}).status_code == 302
    homepage.refresh_from_db()
    assert homepage.business_id == seller.pk
    assert client.post(target, {"business": "", "_save": "1"}).status_code == 302
    homepage.refresh_from_db()
    assert homepage.business is None


def test_login_link_routes_signed_in_roles_to_their_own_panels(client, shops, admin_user):
    assert client.get(reverse("accounts:login")).status_code == 200
    assert client.get(reverse("businesses:dashboard")).status_code == 302
    client.force_login(owner_membership(shops[0]).user)
    assert client.get(reverse("accounts:login")).url == reverse("businesses:dashboard")
    client.force_login(admin_user)
    assert client.get(reverse("accounts:login")).url == reverse("admin:index")
