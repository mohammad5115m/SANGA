"""The colleague directory replaces manually created Contacts."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.businesses.directory import (
    colleague_businesses,
    filter_colleagues,
    get_colleague,
    representative_of,
)
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import invite_member
from apps.core.testing import make_business, make_item, make_user, owner_membership
from apps.pricing.services import ensure_default_tiers


@pytest.fixture
def network(db):
    ensure_default_tiers()
    viewer = make_business(name="سنگ بیننده", owner_phone="09141110001", city="تهران", province="تهران")
    other = make_business(name="سنگ همکار", owner_phone="09141110002", city="محلات", province="مرکزی")
    return {"viewer": viewer, "other": other}


def _login(client, business):
    client.force_login(business.memberships.get(role="owner").user)
    session = client.session
    session["current_business_id"] = str(business.id)
    session.save()


# --- membership of the directory is automatic ---------------------------------


@pytest.mark.django_db
def test_every_active_business_is_a_colleague_without_being_added(network):
    assert network["other"] in colleague_businesses(network["viewer"])
    assert network["viewer"] in colleague_businesses(network["other"])


@pytest.mark.django_db
def test_a_business_is_not_its_own_colleague(network):
    assert network["viewer"] not in colleague_businesses(network["viewer"])


@pytest.mark.django_db
def test_a_suspended_business_disappears_from_the_directory(network):
    network["other"].status = Business.Status.SUSPENDED
    network["other"].save(update_fields=["status"])
    assert network["other"] not in colleague_businesses(network["viewer"])


@pytest.mark.django_db
def test_a_suspended_viewer_sees_no_directory(network):
    network["viewer"].status = Business.Status.SUSPENDED
    network["viewer"].save(update_fields=["status"])
    assert list(colleague_businesses(network["viewer"])) == []


@pytest.mark.django_db
def test_search_matches_name_city_and_phone(network):
    qs = colleague_businesses(network["viewer"])
    assert network["other"] in filter_colleagues(qs, q="همکار")
    assert network["other"] in filter_colleagues(qs, q="محلات")
    assert network["other"] not in filter_colleagues(qs, q="اصفهان")


# --- representative -----------------------------------------------------------


@pytest.mark.django_db
def test_representative_prefers_the_owner(network):
    business = network["other"]
    business.seat_limit = 5
    business.save(update_fields=["seat_limit"])
    invite_member(
        business=business,
        actor_membership=owner_membership(business),
        user=make_user("09141119001"),
    )

    rep = representative_of(business)
    assert rep.role == BusinessMembership.Role.OWNER


# --- pages --------------------------------------------------------------------


@pytest.mark.django_db
def test_colleague_list_page_lists_other_businesses(client, network):
    _login(client, network["viewer"])
    body = client.get(reverse("businesses:colleagues")).content.decode("utf-8")
    assert "سنگ همکار" in body
    assert "لیست همکاران" in body


@pytest.mark.django_db
def test_colleague_detail_shows_their_published_items(client, network):
    make_item(network["other"], lot_code="COL-1", b2b="900000", b2c="1400000")
    make_item(network["other"], lot_code="HID-1", is_visible=False)

    _login(client, network["viewer"])
    url = reverse("businesses:colleague_detail", kwargs={"business_id": network["other"].id})
    body = client.get(url).content.decode("utf-8")

    assert "سنگ همکار" in body
    assert "تراورتن کرم" in body
    # Their unpublished stock is theirs alone, and neither price belongs on a
    # directory page.
    assert "HID-1" not in body
    assert "1400000" not in body.replace(",", "")


@pytest.mark.django_db
def test_colleague_detail_refuses_a_suspended_business(client, network):
    network["other"].status = Business.Status.SUSPENDED
    network["other"].save(update_fields=["status"])

    _login(client, network["viewer"])
    url = reverse("businesses:colleague_detail", kwargs={"business_id": network["other"].id})
    response = client.get(url)
    assert response.status_code == 302
    assert get_colleague(network["viewer"], network["other"].id) is None


@pytest.mark.django_db
def test_there_is_no_manual_colleague_creation_page(client, network):
    """Colleagues are Businesses, so there is nothing for a user to create."""
    from django.urls import NoReverseMatch

    _login(client, network["viewer"])
    with pytest.raises(NoReverseMatch):
        reverse("businesses:colleague_create")

    body = client.get(reverse("businesses:colleagues")).content.decode("utf-8")
    assert "افزودن همکار" not in body
