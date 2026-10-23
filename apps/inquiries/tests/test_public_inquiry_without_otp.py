from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.accounts.models import OTPChallenge, User
from apps.core.testing import make_business, make_item
from apps.inquiries.models import CustomerLead, Inquiry

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop(settings):
    settings.CUSTOMER_OTP_ENABLED = False
    business = make_business(name="استعلام بدون پیامک", owner_phone="09120004701")
    return business, make_item(business, b2c="2000000")


def url(name, shop, **extra):
    return reverse(f"catalog:{name}", kwargs={"storefront_token": shop[0].storefront_token, **extra})


def select(client, shop):
    return client.post(url("inquiry_start", shop, item_id=shop[1].pk))


@pytest.mark.parametrize("entry", ["inquiry_start", "stock_inquiry", "selection_toggle"])
def test_contact_is_saved_without_code_account_or_false_verification(client, shop, entry):
    before = User.objects.count()
    with patch("apps.accounts.services._send_code") as send:
        client.post(url(entry, shop, item_id=shop[1].pk))
        page = client.get(url("inquiry_identify", shop))
        token = page.context["form"].initial["submission_id"]
        assert "دریافت کد" not in page.content.decode()
        response = client.post(
            url("inquiry_identify", shop),
            {
                "name": "مشتری نمونه",
                "phone": "۰۹۱۲۳۴۸۷۶۵۹",
                "message": "برای نمای پروژه",
                "submission_id": str(token),
            },
            follow=True,
        )
        send.assert_not_called()
    assert response.status_code == 200
    assert response.request["PATH_INFO"] == url("inquiry_done", shop)
    inquiry = Inquiry.objects.get()
    assert inquiry.phone == "09123487659" and inquiry.business == shop[0] and inquiry.items.count() == 1
    assert inquiry.lead.phone_verified_at is None and inquiry.requester is None
    assert User.objects.count() == before and not response.wsgi_request.user.is_authenticated
    assert not OTPChallenge.objects.exists()


@pytest.mark.parametrize("phone", ["", "123", "091234567890"])
def test_contact_number_is_still_mandatory_and_validated(client, shop, phone):
    select(client, shop)
    response = client.post(url("inquiry_identify", shop), {"name": "مشتری نمونه", "phone": phone})
    assert response.status_code == 200
    assert response.context["form"].errors
    assert not Inquiry.objects.exists() and not CustomerLead.objects.exists()


def test_repeated_submission_does_not_duplicate_inquiry(client, shop):
    select(client, shop)
    token = client.get(url("inquiry_identify", shop)).context["form"].initial["submission_id"]
    data = {"name": "مشتری نمونه", "phone": "09123487659", "submission_id": str(token)}
    client.post(url("inquiry_identify", shop), data)
    client.post(url("inquiry_identify", shop), data)
    assert Inquiry.objects.count() == 1


def test_disabled_verification_url_neither_submits_nor_marks_verified(client, shop):
    select(client, shop)
    response = client.post(url("inquiry_verify", shop), {"code": "123456", "action": "resend"})
    assert response.url == url("inquiry_identify", shop)
    assert not Inquiry.objects.exists() and not OTPChallenge.objects.exists()


def test_submission_failure_keeps_identity_and_token_for_retry(client, shop):
    select(client, shop)
    token = client.get(url("inquiry_identify", shop)).context["form"].initial["submission_id"]
    data = {"name": "مشتری نمونه", "phone": "09123487659", "submission_id": str(token)}
    with patch("apps.catalog.views_inquiry.submit_public_inquiry", side_effect=RuntimeError("temporary")):
        assert client.post(url("inquiry_identify", shop), data).status_code == 302
    retry = client.get(url("inquiry_identify", shop)).context["form"]
    assert retry["name"].value() == data["name"] and retry["phone"].value() == data["phone"]
    assert str(retry["submission_id"].value()) == str(token)
    assert client.post(url("inquiry_identify", shop), data).status_code == 302
    assert Inquiry.objects.count() == 1


def test_cannot_send_an_inquiry_to_another_seller_with_stale_pending_details(client, shop):
    select(client, shop)
    other = make_business(name="فروشنده دیگر", owner_phone="09120004702")
    session = client.session
    session["public_inquiry_pending"] = {"business_id": str(other.pk), "phone": "09123487659"}
    session.save()
    response = client.get(url("inquiry_identify", shop))
    assert not response.context["form"]["phone"].value()
    assert not Inquiry.objects.exists()
