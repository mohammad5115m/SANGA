"""Platform-admin provisioning boundary (P0).

SANGA has no self-service signup. A Business exists only because an operator
provisioned it, and there must be no authenticated route that creates one.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.urls import NoReverseMatch, reverse

from apps.businesses.models import Business, BusinessMembership

User = get_user_model()


@pytest.mark.django_db
def test_business_creation_route_no_longer_exists():
    with pytest.raises(NoReverseMatch):
        reverse("businesses:onboarding_start")


@pytest.mark.django_db
def test_user_without_a_business_is_sent_to_a_dead_end(client):
    user = User.objects.create_user(phone="09127770001", full_name="بدون کسب‌وکار")
    client.force_login(user)

    response = client.get(reverse("businesses:post_login"))
    assert response.status_code == 302
    assert response.url == reverse("businesses:no_business")

    page = client.get(reverse("businesses:no_business"))
    assert page.status_code == 200
    body = page.content.decode("utf-8")
    # The page explains the situation and points at support; it must not offer a
    # way to self-provision.
    assert "پشتیبانی" in body
    assert 'name="name"' not in body
    assert not Business.objects.exists()


@pytest.mark.django_db
def test_dashboard_redirects_a_businessless_user_without_creating_anything(client):
    user = User.objects.create_user(phone="09127770002")
    client.force_login(user)

    response = client.get(reverse("businesses:dashboard"))
    assert response.status_code == 302
    assert response.url == reverse("businesses:no_business")
    assert not Business.objects.exists()
    assert not BusinessMembership.objects.exists()


@pytest.mark.django_db
def test_capability_gated_view_redirects_a_businessless_user(client):
    user = User.objects.create_user(phone="09127770003")
    client.force_login(user)

    response = client.get(reverse("businesses:team"))
    assert response.status_code == 302
    assert response.url == reverse("businesses:no_business")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name",
    [
        "businesses:dashboard",
        "businesses:team",
        "businesses:settings",
        "marketplace:home",
        "purchase_requests:my_list",
        "purchase_requests:network_list",
        "inventory:lot_list",
        "catalog_manage:list",
        "accounting:index",
    ],
)
def test_every_app_entry_point_survives_a_businessless_user(client, url_name):
    """Guards against a redirect target that no longer resolves.

    Several views redirected to the removed self-service onboarding route. Those
    lines only run when the user has no Business, which is exactly the case no
    other test exercised, so a NoReverseMatch sat there unnoticed.
    """
    user = User.objects.create_user(phone="09127779999")
    client.force_login(user)

    response = client.get(reverse(url_name))
    assert response.status_code in (200, 302)
    if response.status_code == 302:
        assert response.url == reverse("businesses:no_business")


# --- Admin provisioning path -------------------------------------------------


@pytest.mark.django_db
def test_provision_business_command_creates_business_and_owner():
    call_command(
        "provision_business",
        "--name=سنگ آفتاب",
        "--owner-phone=09127770010",
        "--owner-name=مالک آفتاب",
        "--city=اصفهان",
    )

    owner = User.objects.get(phone="09127770010")
    business = Business.objects.get(name="سنگ آفتاب")
    membership = BusinessMembership.objects.get(user=owner, business=business)

    assert owner.full_name == "مالک آفتاب"
    assert business.city == "اصفهان"
    assert membership.role == BusinessMembership.Role.OWNER
    assert membership.status == BusinessMembership.Status.ACTIVE


@pytest.mark.django_db
def test_provision_business_rejects_an_invalid_phone():
    with pytest.raises(CommandError):
        call_command("provision_business", "--name=سنگ خطا", "--owner-phone=12345")
    assert not Business.objects.filter(name="سنگ خطا").exists()


@pytest.mark.django_db
def test_provision_user_command_attaches_to_an_existing_business():
    call_command("provision_business", "--name=سنگ مهتاب", "--owner-phone=09127770020")
    business = Business.objects.get(name="سنگ مهتاب")

    call_command(
        "provision_user",
        "--phone=09127770021",
        f"--business={business.slug}",
        "--full-name=فروشنده یک",
        "--role=staff",
    )

    membership = BusinessMembership.objects.get(
        user__phone="09127770021",
        business=business,
    )
    assert membership.role == BusinessMembership.Role.STAFF
    assert membership.permissions, "role defaults should be materialized on save"


@pytest.mark.django_db
def test_provision_user_refuses_an_unknown_business():
    with pytest.raises(CommandError):
        call_command("provision_user", "--phone=09127770030", "--business=does-not-exist")
    assert not User.objects.filter(phone="09127770030").exists()


@pytest.mark.django_db
def test_provision_user_refuses_a_duplicate_membership():
    call_command("provision_business", "--name=سنگ ستاره", "--owner-phone=09127770040")
    business = Business.objects.get(name="سنگ ستاره")

    call_command("provision_user", "--phone=09127770041", f"--business={business.slug}")
    with pytest.raises(CommandError):
        call_command("provision_user", "--phone=09127770041", f"--business={business.slug}")

    assert BusinessMembership.objects.filter(user__phone="09127770041").count() == 1
