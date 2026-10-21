from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.core.testing import make_business, owner_membership
from apps.inquiries.models import (
    CustomerFollowUp,
    CustomerLead,
    CustomerNote,
    FollowUpReminderRead,
)


def _login(client, business):
    user = owner_membership(business).user
    client.force_login(user)
    session = client.session
    session["current_business_id"] = str(business.pk)
    session.save()
    return user


def _lead(business, *, name="سارا احمدی", phone="09121458732"):
    return CustomerLead.objects.create(
        business=business,
        name=name,
        phone=phone,
        category=CustomerLead.Category.ARCHITECT,
        crm_status=CustomerLead.CRMStatus.NEGOTIATING,
        tags=["پروژه ویلایی", "نمای روشن"],
        current_needs="نمای ویلا، حدود ۳۲۰ متر مربع",
    )


def _followup(business, customer, *, scheduled_for=None):
    scheduled_for = scheduled_for or timezone.now() - timedelta(hours=2)
    return CustomerFollowUp.objects.create(
        business=business,
        customer=customer,
        title="اعلام قیمت نهایی نمای ویلا",
        scheduled_for=scheduled_for,
        reminder_minutes=60,
        remind_at=scheduled_for - timedelta(minutes=60),
        priority=CustomerFollowUp.Priority.URGENT,
        status=CustomerFollowUp.Status.SCHEDULED,
        related_context="تراورتن عباس‌آباد",
    )


@pytest.mark.django_db
def test_operational_crm_renders_persisted_customers_followups_and_reminders(client):
    business = make_business(name="سنگ CRM", owner_phone="09128880001")
    customer = _lead(business)
    CustomerNote.objects.create(
        business=business,
        customer=customer,
        text="نمونه حضوری پسندیده شده است.",
    )
    followup = _followup(business, customer)
    user = _login(client, business)

    customers = client.get(reverse("inquiries:leads"))
    body = customers.content.decode()
    assert customers.status_code == 200
    assert "سارا احمدی" in body
    assert "معمار / طراح" in body
    assert customers.context["leads"][0]["tags"] == ["پروژه ویلایی", "نمای روشن"]
    assert "حالت دمو" not in body

    followups = client.get(reverse("inquiries:followups")).content.decode()
    assert "اعلام قیمت نهایی نمای ویلا" in followups
    assert "عقب‌افتاده" in followups

    notifications = client.get(reverse("notifications:list")).content.decode()
    assert "یادآوری پیگیری" in notifications
    assert "سارا احمدی" in notifications
    assert FollowUpReminderRead.objects.filter(followup=followup, user=user).exists()


@pytest.mark.django_db
def test_profile_notes_and_followups_persist_across_browser_sessions(client):
    business = make_business(name="سنگ اقدام", owner_phone="09128880002")
    customer = _lead(business)
    user = _login(client, business)

    client.post(
        reverse("inquiries:lead_update_profile", kwargs={"lead_id": customer.pk}),
        {
            "category": "builder",
            "crm_status": "active",
            "tags": "نمای روشن، تصمیم‌گیرنده",
            "current_needs": "نمای ساختمان اداری، حدود ۴۰۰ متر مربع",
        },
    )
    client.post(
        reverse("inquiries:lead_add_note", kwargs={"lead_id": customer.pk}),
        {"text": "کارفرما نمونه دوم را هم درخواست کرد."},
    )
    scheduled_for = timezone.localtime() + timedelta(days=3)
    client.post(
        reverse("inquiries:lead_schedule_followup", kwargs={"lead_id": customer.pk}),
        {
            "title": "ارسال تصویر نمونه دوم",
            "scheduled_for": scheduled_for.strftime("%Y-%m-%dT%H:%M"),
            "reminder_minutes": "60",
            "priority": "high",
            "related_context": "تراورتن عباس‌آباد",
            "note": "تصویر در نور روز فرستاده شود.",
        },
    )

    customer.refresh_from_db()
    assert customer.category == CustomerLead.Category.BUILDER
    assert customer.crm_status == CustomerLead.CRMStatus.ACTIVE
    assert customer.tags == ["نمای روشن", "تصمیم‌گیرنده"]
    assert CustomerNote.objects.filter(customer=customer).count() == 1
    followup = CustomerFollowUp.objects.get(customer=customer)
    assert followup.remind_at == followup.scheduled_for - timedelta(minutes=60)

    new_browser = Client()
    new_browser.force_login(user)
    session = new_browser.session
    session["current_business_id"] = str(business.pk)
    session.save()
    persisted = new_browser.get(
        reverse("inquiries:lead_detail", kwargs={"lead_id": customer.pk})
    ).content.decode()
    assert "تصمیم‌گیرنده" in persisted
    assert "کارفرما نمونه دوم را هم درخواست کرد." in persisted
    assert "ارسال تصویر نمونه دوم" in persisted

    new_browser.post(
        reverse("inquiries:followup_complete", kwargs={"followup_id": followup.pk}),
        {"note": "تصویر ارسال و دریافت آن تأیید شد."},
    )
    followup.refresh_from_db()
    assert followup.status == CustomerFollowUp.Status.COMPLETED
    assert followup.completed_at is not None
    assert followup.note == "تصویر ارسال و دریافت آن تأیید شد."


@pytest.mark.django_db
def test_operational_crm_ids_and_mutations_are_tenant_scoped(client):
    first = make_business(name="سنگ اول", owner_phone="09128880003")
    second = make_business(name="سنگ دوم", owner_phone="09128880004")
    customer = _lead(first)
    followup = _followup(first, customer)

    _login(client, second)
    inaccessible = client.get(
        reverse("inquiries:lead_detail", kwargs={"lead_id": customer.pk})
    )
    assert inaccessible.status_code == 302
    assert inaccessible.url == reverse("inquiries:leads")

    client.post(
        reverse("inquiries:lead_add_note", kwargs={"lead_id": customer.pk}),
        {"text": "یادداشت غیرمجاز"},
    )
    client.post(
        reverse("inquiries:followup_complete", kwargs={"followup_id": followup.pk})
    )
    followup.refresh_from_db()
    assert not CustomerNote.objects.filter(customer=customer).exists()
    assert followup.status == CustomerFollowUp.Status.SCHEDULED


@pytest.mark.django_db
def test_rescheduling_a_read_reminder_resets_its_persistent_read_state(client):
    business = make_business(name="سنگ یادآوری", owner_phone="09128880006")
    customer = _lead(business)
    followup = _followup(business, customer)
    user = _login(client, business)
    client.get(reverse("notifications:list"))
    assert FollowUpReminderRead.objects.filter(followup=followup, user=user).exists()

    scheduled_for = timezone.localtime() + timedelta(days=2)
    client.post(
        reverse("inquiries:followup_reschedule", kwargs={"followup_id": followup.pk}),
        {"scheduled_for": scheduled_for.strftime("%Y-%m-%dT%H:%M")},
    )
    followup.refresh_from_db()
    assert followup.status == CustomerFollowUp.Status.SCHEDULED
    assert followup.remind_at == followup.scheduled_for - timedelta(minutes=60)
    assert not FollowUpReminderRead.objects.filter(followup=followup, user=user).exists()


@pytest.mark.django_db
def test_empty_operational_crm_has_no_fictional_customers(client):
    business = make_business(name="سنگ واقعی", owner_phone="09128880005")
    _login(client, business)
    body = client.get(reverse("inquiries:leads")).content.decode()
    assert "سارا احمدی" not in body
    assert "هنوز مشتری‌ای ثبت نشده است" in body
