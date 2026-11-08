"""End-to-end regressions for the owner's account and customer workflows."""

from datetime import timedelta

import pytest
from django.contrib import admin
from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.forms import modelform_factory
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import User
from apps.businesses.models import Business, BusinessMembership
from apps.catalog.models import CustomCatalog, CustomCatalogItem, StorefrontCollection, StorefrontCollectionItem
from apps.core.admin_portal import BusinessProvisionForm
from apps.core.calendar import format_jalali
from apps.core.testing import make_business, make_item, make_product
from apps.inquiries.models import CustomerFollowUp, CustomerLead, CustomerNote, FollowUpReminderRead
from apps.inventory.admin import InventoryLotAdminForm
from apps.inventory.models import InventoryLot

pytestmark = pytest.mark.django_db
PASSWORD = "Console-Setup-Test-84!"


@pytest.fixture
def owner(client):
    user = User.objects.create_superuser(phone="09128887001", password=PASSWORD)
    client.force_login(user)
    return user


@pytest.fixture
def business():
    value = make_business(name="کسب‌وکار آزمایش مدیریت", owner_phone="09128887002")
    value.seat_limit = 3
    value.save(update_fields=["seat_limit"])
    return value


def account_payload(kind, business=None, **changes):
    if kind == "business":
        data = {
            "name": "کسب‌وکار حساب جدید", "owner_phone": "۰۹۱۲۸۸۸۷۰۰۳", "owner_name": "مالک جدید",
            "plan": Business.Plan.SELLER, "seat_limit": 2,
        }
    else:
        data = {
            "business": str(business.pk), "phone": "۰۹۱۲۸۸۸۷۰۰۳", "full_name": "عضو جدید",
            "role": BusinessMembership.Role.STAFF,
        }
    return {**data, "password1": PASSWORD, "password2": PASSWORD, **changes}


@pytest.mark.parametrize("kind", ["business", "member"])
def test_provisioned_account_can_actually_log_in(client, owner, business, kind):
    response = client.post(reverse("admin:provision_" + kind), account_payload(kind, business))
    assert response.status_code == 302
    user = User.objects.get(phone="09128887003")
    assert user.check_password(PASSWORD)
    assert user.password != PASSWORD
    client.logout()
    response = client.post(reverse("accounts:login"), {"phone": user.phone, "password": PASSWORD})
    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)


@pytest.mark.parametrize("kind", ["business", "member"])
@pytest.mark.parametrize("passwords", [("", ""), ("short", "short"), (PASSWORD, "different")])
def test_invalid_credentials_leave_no_partial_account(client, owner, business, kind, passwords):
    before = (
        User.objects.count(), Business.objects.count(), BusinessMembership.objects.count(), LogEntry.objects.count(),
    )
    response = client.post(reverse("admin:provision_" + kind), account_payload(
        kind, business, password1=passwords[0], password2=passwords[1],
    ))
    assert response.status_code == 200
    assert response.context["form"].errors
    assert PASSWORD not in response.content.decode()
    assert before == (User.objects.count(), Business.objects.count(), BusinessMembership.objects.count(),
                      LogEntry.objects.count())


@pytest.mark.parametrize("kind", ["business", "member"])
def test_existing_credentials_are_preserved(client, owner, business, kind):
    existing = User.objects.create_user(phone="09128887003", password=PASSWORD)
    encoded = existing.password
    data = account_payload(kind, business, password1="", password2="")
    assert client.post(reverse("admin:provision_" + kind), data).status_code == 302
    existing.refresh_from_db()
    assert existing.password == encoded


@pytest.mark.parametrize("kind", ["business", "member"])
def test_provisioning_cannot_reset_an_existing_password(client, owner, business, kind):
    existing = User.objects.create_user(phone="09128887003", password=PASSWORD)
    encoded = existing.password
    response = client.post(reverse("admin:provision_" + kind), account_payload(kind, business))
    assert response.status_code == 200
    assert "password1" in response.context["form"].errors
    existing.refresh_from_db()
    assert existing.password == encoded
    assert not existing.memberships.exists()


def test_a_concurrently_created_account_is_not_overwritten(client, owner, monkeypatch):
    original = BusinessProvisionForm.is_valid

    def validate_and_create(form):
        valid = original(form)
        if valid:
            User.objects.create_user(phone="09128887003", password="Existing-Password-39!")
        return valid

    monkeypatch.setattr(BusinessProvisionForm, "is_valid", validate_and_create)
    response = client.post(reverse("admin:provision_business"), account_payload("business"))
    assert response.status_code == 200
    user = User.objects.get(phone="09128887003")
    assert user.check_password("Existing-Password-39!")
    assert not Business.objects.filter(name="کسب‌وکار حساب جدید").exists()
    assert not user.memberships.exists()


def test_optional_otp_flow_still_creates_an_unusable_password(client, owner, settings):
    settings.AUTH_LOGIN_METHOD = "otp"
    response = client.post(reverse("admin:provision_business"), account_payload(
        "business", password1="", password2="",
    ))
    assert response.status_code == 302
    assert not User.objects.get(phone="09128887003").has_usable_password()


def test_no_business_explains_how_to_add_a_member(client, owner):
    response = client.get(reverse("admin:provision_member"))
    assert response.context["needs_business"]
    assert "ابتدا یک کسب‌وکار فعال بسازید" in response.content.decode()
    assert 'type="submit" class="portal-button" disabled' in response.content.decode()


@pytest.mark.parametrize("app", ["businesses", "invoicing", "accounts", "inventory", "inquiries", "catalog"])
def test_section_pages_show_their_models_without_querying_global_metrics(client, owner, app):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("admin:app_list", kwargs={"app_label": app}))
    assert response.status_code == 200
    assert [entry["app_label"] for entry in response.context["app_list"]] == [app]
    assert 'class="portal-app-index"' in response.content.decode()
    assert 'class="portal-dashboard"' not in response.content.decode()
    assert len(queries) <= 8


def test_dashboard_activity_uses_jalali_dates_and_pending_link_is_filtered(client, owner, business):
    entry = LogEntry.objects.create(
        user=owner, content_type=ContentType.objects.get_for_model(business), object_id=str(business.pk),
        object_repr=business.name, action_flag=ADDITION,
    )
    response = client.get(reverse("admin:index"))
    assert format_jalali(entry.action_time, with_time=True) in response.content.decode()
    target = response.context["admin_pending_businesses_url"]
    assert "verification_status__exact=pending" in target
    assert client.get(target).context["cl"].queryset.count() == 0


@pytest.mark.parametrize("query", ["۰۹۱۲۸۸۸۷۰۰۲", "+98 912 888 7002", "٠٩١٢٨٨٨٧٠٠٢"])
def test_phone_search_matches_localized_and_international_input(client, owner, business, query):
    for route in ("admin:index", "admin:accounts_user_changelist", "admin:businesses_business_changelist"):
        response = client.get(reverse(route), {"q": query})
        assert response.status_code == 200
        if route == "admin:index":
            assert business.name in response.content.decode()
        else:
            assert response.context["cl"].queryset.count() == 1


def test_platform_owner_cannot_delete_or_deactivate_their_own_account(client, owner):
    assert client.post(reverse("admin:accounts_user_delete", args=[owner.pk]), {"post": "yes"}).status_code == 403
    response = client.get(reverse("admin:accounts_user_change", args=[owner.pk]))
    assert "is_active" not in response.context["adminform"].form.fields
    response = client.post(reverse("admin:accounts_user_change", args=[owner.pk]), {
        "phone": owner.phone, "date_joined": owner.date_joined.isoformat(), "is_active": "", "_save": "1",
    })
    assert response.status_code == 302
    owner.refresh_from_db()
    assert owner.is_active
    assert client.get(reverse("admin:index")).status_code == 200


@pytest.mark.parametrize("phone,valid", [("+98 912 888 7002", True), ("۱۲۳۴۵", False)])
def test_account_edits_validate_and_normalize_the_login_phone(client, owner, business, phone, valid):
    user = business.memberships.get(role=BusinessMembership.Role.OWNER).user
    response = client.post(reverse("admin:accounts_user_change", args=[user.pk]), {
        "phone": phone, "date_joined": user.date_joined.isoformat(), "is_active": "on", "_save": "1",
    })
    assert response.status_code == (302 if valid else 200)
    user.refresh_from_db()
    assert user.phone == "09128887002"


@pytest.mark.parametrize("kind", ["note", "followup"])
def test_customer_forms_reject_a_customer_from_another_business(client, owner, business, kind):
    other = make_business(name="کسب‌وکار دیگر", owner_phone="09128887004")
    customer = CustomerLead.objects.create(business=other, phone="09128887005", name="مشتری دیگر")
    data = {
        "business": str(business.pk), "customer": str(customer.pk), "author": str(owner.pk),
        "text": "یادداشت", "title": "پیگیری", "scheduled_for": "2026-11-20T10:00",
        "reminder_minutes": "60", "status": "scheduled", "priority": "normal", "_save": "1",
    }
    response = client.post(reverse("admin:inquiries_customer" + kind + "_add"), data)
    assert response.status_code == 200
    assert "مشتری باید متعلق به همین کسب‌وکار باشد" in response.content.decode()
    assert not CustomerNote.objects.exists()
    assert not CustomerFollowUp.objects.exists()


def test_followup_dates_are_derived_and_rescheduling_reopens_reminders(client, owner, business):
    customer = CustomerLead.objects.create(business=business, phone="09128887005", name="مشتری یادآوری")
    data = {
        "business": str(business.pk), "customer": str(customer.pk), "title": "پیگیری", "scheduled_for":
        "2026-11-20T10:00", "reminder_minutes": "60", "status": "scheduled", "priority": "normal", "_save": "1",
    }
    response = client.post(reverse("admin:inquiries_customerfollowup_add"), data)
    assert response.status_code == 302
    followup = CustomerFollowUp.objects.get()
    assert followup.remind_at == followup.scheduled_for - timedelta(minutes=60)
    assert followup.completed_at is None
    FollowUpReminderRead.objects.create(followup=followup, user=owner)
    data.update(scheduled_for="2026-11-21T10:00", reminder_minutes="15")
    assert client.post(reverse("admin:inquiries_customerfollowup_change", args=[followup.pk]), data).status_code == 302
    followup.refresh_from_db()
    assert followup.remind_at == followup.scheduled_for - timedelta(minutes=15)
    assert not followup.reminder_reads.exists()
    data.update(status="completed")
    assert client.post(reverse("admin:inquiries_customerfollowup_change", args=[followup.pk]), data).status_code == 302
    followup.refresh_from_db()
    assert followup.completed_at is not None


def test_customer_cannot_move_tenants_and_duplicate_phone_is_a_form_error(client, owner, business):
    other = make_business(name="کسب‌وکار مقصد", owner_phone="09128887004")
    lead = CustomerLead.objects.create(business=business, phone="09128887005", name="مشتری اول")
    CustomerLead.objects.create(business=business, phone="09128887006", name="مشتری دوم")
    data = {
        "business": str(other.pk), "phone": "09128887006", "name": lead.name, "category": lead.category,
        "crm_status": lead.crm_status, "tags": "[]", "_save": "1",
    }
    response = client.post(reverse("admin:inquiries_customerlead_change", args=[lead.pk]), data)
    assert response.status_code == 200
    lead.refresh_from_db()
    assert lead.business_id == business.pk
    assert lead.phone == "09128887005"
    data["phone"] = lead.phone
    assert client.post(reverse("admin:inquiries_customerlead_change", args=[lead.pk]), data).status_code == 302
    lead.refresh_from_db()
    assert lead.business_id == business.pk


def test_cross_tenant_product_is_a_form_error_before_inventory_save(owner, business):
    other = make_business(name="محصول کسب‌وکار دیگر", owner_phone="09128887004")
    product = make_product(other)
    form_class = modelform_factory(InventoryLot, form=InventoryLotAdminForm, fields=("business", "product"))
    form = form_class(data={"business": str(business.pk), "product": str(product.pk)})
    assert not form.is_valid()
    assert "محصول و موجودی" in str(form.errors["product"])
    assert not InventoryLot.objects.exists()


@pytest.mark.parametrize("kind", ["catalog", "collection"])
@pytest.mark.parametrize("saved", [True, False])
def test_catalog_items_and_inlines_reject_products_from_another_tenant(owner, business, kind, saved):
    other = make_business(name="محصول ویترین دیگر", owner_phone="09128887004")
    lot = make_item(other)
    parent_model, child_model = (
        (CustomCatalog, CustomCatalogItem) if kind == "catalog" else (StorefrontCollection, StorefrontCollectionItem)
    )
    parent = parent_model(business=business, title="مجموعه آزمایشی")
    if saved:
        parent.save()
    request = RequestFactory().get("/admin/")
    request.user = owner
    child_admin = admin.site._registry[child_model]
    if saved:
        form_class = child_admin.get_form(request)
        form = form_class(data={kind: str(parent.pk), "lot": str(lot.pk), "sort_order": "0"})
        assert not form.is_valid()
        assert "lot" in form.errors
    parent_admin = admin.site._registry[parent_model]
    inline = parent_admin.inlines[0](parent_model, admin.site)
    formset_class = inline.get_formset(request, obj=parent)
    prefix = formset_class.get_default_prefix()
    formset = formset_class(data={
        f"{prefix}-TOTAL_FORMS": "1", f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-0-lot": str(lot.pk), f"{prefix}-0-sort_order": "0",
    }, instance=parent, prefix=prefix)
    assert not formset.is_valid()
    assert "lot" in formset.forms[0].errors
    assert not child_model.objects.exists()


def test_existing_products_and_collections_keep_their_tenant(owner, business):
    request = RequestFactory().get("/admin/")
    request.user = owner
    product = make_product(business)
    lot = make_item(business, product=product)
    records = [product, lot, CustomCatalog.objects.create(business=business, title="کاتالوگ ثابت"),
               StorefrontCollection.objects.create(business=business, title="ویترین ثابت")]
    for obj in records:
        model_admin = admin.site._registry[type(obj)]
        assert model_admin.get_form(request, obj=obj).base_fields["business"].disabled
        assert not model_admin.get_form(request).base_fields["business"].disabled


@pytest.mark.parametrize("minutes", [-1, 10081])
def test_followup_reminder_bounds_are_reported_before_save(client, owner, business, minutes):
    lead = CustomerLead.objects.create(business=business, phone="09128887005", name="مشتری بازه")
    response = client.post(reverse("admin:inquiries_customerfollowup_add"), {
        "business": str(business.pk), "customer": str(lead.pk), "title": "پیگیری",
        "scheduled_for": "2026-11-20T10:00", "reminder_minutes": str(minutes),
        "status": "scheduled", "priority": "normal", "_save": "1",
    })
    assert response.status_code == 200
    assert "reminder_minutes" in response.context["adminform"].form.errors
    assert not CustomerFollowUp.objects.exists()
