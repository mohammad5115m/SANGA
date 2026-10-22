from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.testing import make_business, owner_membership
from apps.inquiries.crm import CRMRepository
from apps.inquiries.models import CustomerFollowUp, CustomerLead, CustomerNote, Inquiry


@pytest.fixture
def crm(client, db):
    business = make_business(name="سنگ آزمون CRM", owner_phone="09129997001")
    member = owner_membership(business)
    client.force_login(member.user)
    customer = CustomerLead.objects.create(business=business, name="مشتری آزمایش", phone="09129997002")
    now = timezone.now()
    followup = CustomerFollowUp.objects.create(
        business=business, customer=customer, title="ارسال نمونه", scheduled_for=now, remind_at=now,
    )
    inquiry = Inquiry.objects.create(business=business, lead=customer, name=customer.name, phone=customer.phone)
    return business, member, customer, followup, inquiry


@pytest.mark.parametrize("restricted", ["suspended", "expired"])
@pytest.mark.parametrize("action", [
    "lead_add_note", "lead_update_profile", "lead_schedule_followup", "lead_record_followup",
    "followup_complete", "followup_postpone", "followup_reschedule", "followup_cancel", "set_status",
])
def test_restricted_business_cannot_mutate_crm(client, crm, restricted, action):
    business, _, customer, followup, inquiry = crm
    if restricted == "suspended":
        business.status = Business.Status.SUSPENDED
    else:
        business.active_until = timezone.localdate() - timedelta(days=1)
    business.save()
    before_customer = CustomerLead.objects.filter(pk=customer.pk).values().get()
    before_followup = CustomerFollowUp.objects.filter(pk=followup.pk).values().get()
    data = {
        "text": "یادداشت ممنوع", "category": "builder", "crm_status": "active",
        "current_needs": "نیاز جدید", "title": "پیگیری ممنوع", "note": "نتیجه ممنوع",
        "scheduled_for": (timezone.localtime() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        "priority": "normal", "reminder_minutes": "60", "status": "contacted",
    }
    pk = inquiry.pk if action == "set_status" else (followup.pk if action.startswith("followup_") else customer.pk)
    response = client.post(reverse(f"inquiries:{action}", args=[pk]), data)
    assert response.status_code in (200, 302)
    assert not CustomerNote.objects.filter(customer=customer).exists()
    assert CustomerLead.objects.filter(pk=customer.pk).values().get() == before_customer
    assert CustomerFollowUp.objects.filter(pk=followup.pk).values().get() == before_followup
    assert CustomerFollowUp.objects.filter(customer=customer).count() == 1
    inquiry.refresh_from_db()
    assert inquiry.status == Inquiry.Status.NEW
    assert client.get(reverse("inquiries:lead_detail", args=[customer.pk])).status_code == 200


@pytest.mark.parametrize("action,form_name,data,field", [
    ("lead_add_note", "note_form", {"text": "ی" * 1001}, "text"),
    ("lead_update_profile", "profile_form", {
        "category": "invalid", "crm_status": "active", "current_needs": "نیاز نوشته‌شده حفظ شود",
    }, "current_needs"),
    ("lead_schedule_followup", "followup_form", {
        "title": "ارسال نمونه نوشته‌شده", "scheduled_for": "invalid", "note": "متن پیگیری",
        "reminder_minutes": "60", "priority": "normal",
    }, "title"),
    ("lead_record_followup", "complete_form", {"note": "ن" * 1001}, "note"),
])
@pytest.mark.parametrize("boosted", [False, True])
def test_customer_form_errors_preserve_input_and_canonical_location(
    client, crm, action, form_name, data, field, boosted,
):
    _, _, customer, _, _ = crm
    response = client.post(reverse(f"inquiries:{action}", args=[customer.pk]), data,
                           **({"HTTP_HX_REQUEST": "true"} if boosted else {}))
    assert response.status_code == 200
    form = response.context[form_name]
    assert form.is_bound and form.errors and form[field].value() == data[field]
    assert data[field] in response.content.decode()
    assert response["HX-Replace-Url"] == reverse("inquiries:lead_detail", args=[customer.pk])
    assert 'aria-invalid="true"' in response.content.decode()


def test_crm_repository_checks_actor_membership_without_view_decorators(crm):
    business, member, customer, _, _ = crm
    member.permissions = []
    member.role = "viewer"
    member.save()
    repository = CRMRepository(SimpleNamespace(business=business, user=member.user, membership=member))
    with pytest.raises(ValueError, match="دسترسی"):
        repository.add_note(customer.pk, "ممنوع")
    assert not CustomerNote.objects.filter(customer=customer).exists()
