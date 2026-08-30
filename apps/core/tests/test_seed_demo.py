from datetime import timedelta

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Business, BusinessMembership
from apps.catalog.models import StorefrontCollection
from apps.inventory.models import InventoryLot

DEMO_PHONE = "09121111111"


@pytest.mark.django_db
def test_seed_demo_creates_provisioned_demo_login(client, settings):
    settings.SMS_PROVIDER = "console"
    assert not User.objects.filter(phone=DEMO_PHONE).exists()

    call_command("seed_demo")

    user = User.objects.get(phone=DEMO_PHONE)
    membership = BusinessMembership.objects.get(
        user=user,
        role=BusinessMembership.Role.OWNER,
    )
    business = membership.business
    assert user.is_active is True
    assert membership.status == BusinessMembership.Status.ACTIVE
    assert business.status == Business.Status.ACTIVE
    assert business.is_onboarded is True

    special = InventoryLot.objects.get(business=business, lot_code="DEMO-001")
    b2c = special.prices.select_related("tier").get(tier__code="b2c")
    assert b2c.special_is_live is True
    assert business.storefront_collections.filter(
        suggestion_kind=StorefrontCollection.SuggestionKind.ECONOMIC,
        is_active=True,
        items__lot=special,
    ).exists()
    storefront = client.get(
        reverse(
            "catalog:storefront",
            kwargs={"storefront_token": business.storefront_token},
        )
    ).content.decode()
    assert "فروش ویژه" in storefront
    assert "انتخاب‌های اقتصادی" in storefront

    client.post(reverse("accounts:login"), {"phone": DEMO_PHONE}, follow=False)
    response = client.get(reverse("accounts:verify"))
    assert response.status_code == 200
    dev_code = response.context["dev_code"]
    assert dev_code

    response = client.post(
        reverse("accounts:verify"),
        {"phone": DEMO_PHONE, "code": dev_code},
        follow=False,
    )
    assert response.status_code == 302
    assert response.url == reverse("businesses:post_login")


@pytest.mark.django_db
def test_seed_demo_repairs_disabled_demo_login():
    call_command("seed_demo")

    user = User.objects.get(phone=DEMO_PHONE)
    membership = BusinessMembership.objects.get(
        user=user,
        role=BusinessMembership.Role.OWNER,
    )
    business = membership.business

    user.is_active = False
    user.save(update_fields=["is_active"])
    membership.status = BusinessMembership.Status.SUSPENDED
    membership.save(update_fields=["status"])
    business.status = Business.Status.SUSPENDED
    business.verification_status = Business.VerificationStatus.SUSPENDED
    business.active_until = timezone.localdate() - timedelta(days=1)
    business.save(
        update_fields=[
            "status",
            "verification_status",
            "active_until",
            "updated_at",
        ]
    )

    call_command("seed_demo")

    user.refresh_from_db()
    membership.refresh_from_db()
    business.refresh_from_db()
    assert user.is_active is True
    assert membership.status == BusinessMembership.Status.ACTIVE
    assert business.status == Business.Status.ACTIVE
    assert business.verification_status == Business.VerificationStatus.VERIFIED
    assert business.active_until is None
