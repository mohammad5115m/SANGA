from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.testing import make_business, owner_membership


def _login(client, business):
    client.force_login(owner_membership(business).user)
    session = client.session
    session["current_business_id"] = str(business.pk)
    session.save()


@pytest.mark.django_db
@override_settings(SANGA_CRM_DEMO_ENABLED=True)
def test_demo_crm_has_customer_work_queue_and_due_notifications(client):
    business = make_business(name="سنگ CRM", owner_phone="09128880001")
    _login(client, business)

    customers = client.get(reverse("inquiries:leads"))
    body = customers.content.decode()
    assert customers.status_code == 200
    assert "سارا احمدی" in body
    assert "معمار / طراح" in body
    assert "محصولات درخواستی" in body

    followups = client.get(reverse("inquiries:followups")).content.decode()
    assert "عقب‌افتاده" in followups
    assert "امروز" in followups
    assert "آینده" in followups
    assert "انجام‌شده" in followups
    assert "اعلام قیمت نهایی نمای ویلا" in followups

    notifications = client.get(reverse("notifications:list")).content.decode()
    assert "یادآوری پیگیری" in notifications
    assert "سارا احمدی" in notifications


@pytest.mark.django_db
@override_settings(SANGA_CRM_DEMO_ENABLED=True)
def test_demo_actions_are_meaningful_and_stay_in_the_current_session(client):
    business = make_business(name="سنگ اقدام", owner_phone="09128880002")
    _login(client, business)
    response = client.get(reverse("inquiries:leads"))
    sara = next(item for item in response.context["leads"] if item["name"] == "سارا احمدی")

    profile_response = client.post(
        reverse("inquiries:lead_update_profile", kwargs={"lead_id": sara["id"]}),
        {
            "category": "builder",
            "crm_status": "active",
            "tags": "نمای روشن، تصمیم‌گیرنده",
            "current_needs": "نمای ساختمان اداری، حدود ۴۰۰ متر مربع",
        },
        follow=True,
    )
    profile_body = profile_response.content.decode()
    assert "سازنده" in profile_body
    assert "تصمیم‌گیرنده" in profile_body
    assert "نمای ساختمان اداری" in profile_body

    note_response = client.post(
        reverse("inquiries:lead_add_note", kwargs={"lead_id": sara["id"]}),
        {"text": "کارفرما نمونه دوم را هم درخواست کرد."},
        follow=True,
    )
    assert "کارفرما نمونه دوم را هم درخواست کرد." in note_response.content.decode()

    scheduled_for = timezone.localtime() + timedelta(days=3)
    schedule_response = client.post(
        reverse("inquiries:lead_schedule_followup", kwargs={"lead_id": sara["id"]}),
        {
            "title": "ارسال تصویر نمونه دوم",
            "scheduled_for": scheduled_for.strftime("%Y-%m-%dT%H:%M"),
            "reminder_minutes": "60",
            "priority": "high",
            "related_context": "تراورتن عباس‌آباد",
            "note": "تصویر در نور روز فرستاده شود.",
        },
        follow=True,
    )
    assert "پیگیری بعدی زمان‌بندی شد" in schedule_response.content.decode()
    queue_response = client.get(reverse("inquiries:followups") + "?state=upcoming")
    queue = queue_response.content.decode()
    assert "ارسال تصویر نمونه دوم" in queue
    assert "تراورتن عباس‌آباد" in queue
    created = next(
        item for item in queue_response.context["followups"] if item["title"] == "ارسال تصویر نمونه دوم"
    )
    complete_response = client.post(
        reverse("inquiries:followup_complete", kwargs={"followup_id": created["id"]}),
        follow=True,
    )
    assert "پیگیری انجام‌شده ثبت شد" in complete_response.content.decode()
    completed = client.get(reverse("inquiries:followups") + "?state=completed").content.decode()
    assert "ارسال تصویر نمونه دوم" in completed


@pytest.mark.django_db
@override_settings(SANGA_CRM_DEMO_ENABLED=True)
def test_demo_customer_ids_are_scoped_to_the_current_business(client):
    first = make_business(name="سنگ اول", owner_phone="09128880003")
    second = make_business(name="سنگ دوم", owner_phone="09128880004")
    _login(client, first)
    response = client.get(reverse("inquiries:leads"))
    first_customer_id = response.context["leads"][0]["id"]

    _login(client, second)
    inaccessible = client.get(
        reverse("inquiries:lead_detail", kwargs={"lead_id": first_customer_id})
    )
    assert inaccessible.status_code == 302
    assert inaccessible.url == reverse("inquiries:leads")


@pytest.mark.django_db
@override_settings(SANGA_CRM_DEMO_ENABLED=False)
def test_fictional_customers_are_fail_closed_when_demo_mode_is_off(client):
    business = make_business(name="سنگ واقعی", owner_phone="09128880005")
    _login(client, business)
    body = client.get(reverse("inquiries:leads")).content.decode()
    assert "سارا احمدی" not in body
    assert "هنوز مشتری‌ای ثبت نشده است" in body
