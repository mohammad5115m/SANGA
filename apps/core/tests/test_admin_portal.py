"""The platform owner's console must be useful without bypassing tenant rules."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.admin.sites import site
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.services import request_login_otp
from apps.businesses.models import Business, BusinessMembership
from apps.core.admin_export import csv_response
from apps.invoicing.models import SalesInvoice


@pytest.fixture
def platform_owner(db):
    return User.objects.create_superuser(phone="09129990001", password="unused")


@pytest.mark.django_db
def test_only_platform_owner_can_open_console(client, platform_owner):
    index = reverse("admin:index")
    assert client.get(index).status_code == 302

    employee = User.objects.create_user(phone="09129990002", is_staff=True)
    client.force_login(employee)
    assert client.get(index).status_code == 302
    assert client.get(reverse("admin:provision_business")).status_code == 302

    client.force_login(platform_owner)
    response = client.get(index)
    assert response.status_code == 200
    assert "نمای کلی سنگ اصفهان" in response.content.decode()
    boosted = client.get(index, HTTP_HX_REQUEST="true")
    assert boosted.status_code == 204
    assert boosted["HX-Redirect"] == index
    assert client.get(reverse("admin:provision_business")).status_code == 200
    assert client.get(reverse("admin:provision_member"), {"business": "invalid-id"}).status_code == 200


@pytest.mark.django_db
def test_admin_cannot_bypass_owner_and_seat_provisioning(client, platform_owner):
    client.force_login(platform_owner)
    assert client.get(reverse("admin:businesses_business_add")).status_code == 403
    assert client.get(reverse("admin:businesses_businessmembership_add")).status_code == 403
    business_list = client.get(reverse("admin:businesses_business_changelist"))
    business_page = business_list.content.decode()
    assert reverse("admin:provision_business") in business_page
    assert "<h1>کسب‌وکارها</h1>" in business_page
    assert "براساس وضعیت تأیید" in business_page
    assert "براساس وضعیت فعالیت" in business_page
    assert "CITY" not in business_page
    member_list = client.get(reverse("admin:businesses_businessmembership_changelist"))
    member_page = member_list.content.decode()
    assert reverse("admin:provision_member") in member_page
    assert "<h1>عضویت‌ها</h1>" in member_page
    assert "براساس نقش" in member_page
    assert "براساس وضعیت عضویت" in member_page
    assert "حذف عضویت‌ها" not in member_page
    business_admin = site._registry[Business]
    member_admin = site._registry[BusinessMembership]
    request = type("Request", (), {"user": platform_owner})()
    inline = business_admin.inlines[0](Business, site)
    assert inline.has_add_permission(request) is False
    assert inline.has_change_permission(request) is False
    assert inline.has_delete_permission(request) is False
    assert member_admin.has_delete_permission(request) is False


@pytest.mark.django_db
@pytest.mark.parametrize("boosted", [False, True])
def test_platform_owner_otp_login_opens_admin(client, settings, platform_owner, boosted):
    settings.SMS_PROVIDER = "console"
    challenge = request_login_otp(platform_owner.phone)
    session = client.session
    session["otp_phone"] = platform_owner.phone
    session.save()
    response = client.post(
        reverse("accounts:verify"),
        {"phone": platform_owner.phone, "code": challenge.dev_code},
        **({"HTTP_HX_REQUEST": "true"} if boosted else {}),
    )
    if boosted:
        assert response.status_code == 204
        assert response["HX-Redirect"] == reverse("admin:index")
    else:
        assert response.status_code == 302
        assert response.url == reverse("admin:index")


@pytest.mark.django_db
def test_business_and_member_provisioning_respect_seat_limit(client, platform_owner):
    client.force_login(platform_owner)
    response = client.post(reverse("admin:provision_business"), {
        "name": "سنگ آزمایشگاه",
        "owner_phone": "۰۹۱۲۹۹۹۰۰۰۳",
        "owner_name": "مالک آزمایش",
        "city": "اصفهان",
        "province": "اصفهان",
        "plan": Business.Plan.SELLER,
        "seat_limit": 2,
    })
    assert response.status_code == 302
    business = Business.objects.get(name="سنگ آزمایشگاه")
    assert business.verification_status == Business.VerificationStatus.PENDING
    owner_membership = business.memberships.get(role=BusinessMembership.Role.OWNER)
    assert owner_membership.user.phone == "09129990003"
    owner_page = client.get(reverse("admin:businesses_businessmembership_change", args=[owner_membership.pk]))
    assert owner_page.status_code == 200
    assert "دسترسی کامل مالک" in owner_page.content.decode()
    assert 'name="_save"' not in owner_page.content.decode()

    member_url = reverse("admin:provision_member")
    response = client.post(member_url, {
        "business": str(business.pk), "phone": "09129990004",
        "full_name": "کاربر آزمایش", "role": BusinessMembership.Role.STAFF,
    })
    assert response.status_code == 302
    assert business.memberships.count() == 2
    staff_membership = business.memberships.get(user__phone="09129990004")
    staff_page = client.get(reverse("admin:businesses_businessmembership_change", args=[staff_membership.pk]))
    assert staff_page.status_code == 200
    assert "وضعیت عضویت" in staff_page.content.decode()

    response = client.post(member_url, {
        "business": str(business.pk), "phone": "09129990005",
        "full_name": "کاربر اضافه", "role": BusinessMembership.Role.VIEWER,
    })
    assert response.status_code == 200
    assert business.memberships.count() == 2
    assert not User.objects.filter(phone="09129990005").exists()


@pytest.mark.django_db
def test_dashboard_search_and_invoice_export(client, platform_owner):
    client.force_login(platform_owner)
    invoice_list = client.get(reverse("admin:invoicing_salesinvoice_changelist"))
    invoice_page = invoice_list.content.decode()
    assert "<h1>فاکتورهای فروش</h1>" in invoice_page
    assert "براساس نوع خریدار" in invoice_page
    assert "اضافه‌کردن فاکتور فروش" not in invoice_page
    assert client.get(reverse("admin:invoicing_salesinvoice_add")).status_code == 403

    business = Business.objects.create(name="سنگ نگین", phone="09120000000")
    response = client.get(reverse("admin:index"), {"q": "نگین"})
    assert response.status_code == 200
    assert "سنگ نگین" in response.content.decode()
    response = client.get(reverse("admin:index"), {"q": "۰۹۱۲۰۰۰۰۰۰۰"})
    assert "سنگ نگین" in response.content.decode()

    buyer = Business.objects.create(name="خریدار آزمایشی")
    invoice = SalesInvoice.objects.create(
        seller_business=business,
        buyer_business=buyer,
        buyer_name="خریدار آزمایشی",
        issue_date=timezone.localdate(),
        total_amount=Decimal("120000"),
    )
    response = client.post(reverse("admin:invoicing_salesinvoice_changelist"), {
        "action": "export_selected_csv", "_selected_action": str(invoice.pk), "index": "0",
    })
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "خریدار آزمایشی" in response.content.decode("utf-8-sig")
    assert SalesInvoice.objects.get(pk=invoice.pk).total_amount == Decimal("120000")


@pytest.mark.django_db
def test_ledger_export_is_available_to_view_only_owner(platform_owner):
    from apps.accounting.models import LedgerEntry

    model_admin = site._registry[LedgerEntry]
    request = type("Request", (), {"user": platform_owner, "GET": {}})()
    assert model_admin.has_change_permission(request) is False
    assert "export_selected_csv" in model_admin.get_actions(request)


def test_export_escapes_spreadsheet_formulas():
    response = csv_response("test.csv", ("شرح",), [("=SUM(1,1)",)])
    assert "'=SUM(1,1)" in response.content.decode("utf-8-sig")
