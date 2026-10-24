from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import BusinessMembership
from apps.core.testing import make_business, make_user, owner_membership
from apps.inquiries.models import CustomerFollowUp, CustomerLead, FollowUpReminderRead
from apps.notifications.models import Notification

pytestmark = pytest.mark.django_db


@pytest.fixture
def center(client):
    business = make_business(name="اعلان اصلی", owner_phone="09380001001")
    other = make_business(name="اعلان دیگر", owner_phone="09380001002")
    user = owner_membership(business).user
    BusinessMembership.objects.create(user=user, business=other, role="staff")
    client.force_login(user)
    session = client.session
    session["current_business_id"] = str(business.pk)
    session.save()
    own = Notification.objects.create(user=user, business=business, title="اعلان همین کسب‌وکار")
    foreign = Notification.objects.create(user=user, business=other, title="اعلان کسب‌وکار دیگر")
    general = Notification.objects.create(user=user, title="اعلان شخصی حساب")
    lead = CustomerLead.objects.create(business=business, name="مشتری یادآوری", phone="09380001003")
    followup = CustomerFollowUp.objects.create(
        business=business, customer=lead, title="تماس با مشتری",
        scheduled_for=timezone.now(), remind_at=timezone.now() - timedelta(minutes=10),
    )
    return business, other, user, own, foreign, general, followup


def test_reading_the_list_does_not_mark_anything_read(client, center):
    _, _, _, own, foreign, general, followup = center
    response = client.get(reverse("notifications:list"))
    assert response.status_code == 200
    assert not Notification.objects.filter(pk__in=[own.pk, foreign.pk, general.pk], is_read=True).exists()
    assert not FollowUpReminderRead.objects.filter(followup=followup).exists()


def test_list_and_badge_share_the_current_business_scope(client, center):
    _, _, _, own, foreign, general, _ = center
    response = client.get(reverse("notifications:list"))
    body = response.content.decode()
    assert own.title in body and general.title in body and foreign.title not in body
    assert response.context["notification_badge_count"] == 3


def test_older_notifications_remain_reachable_and_unread_filter_is_paged(client, center):
    business, _, user, own, _, _, _ = center
    # Fast inserts can share a timestamp (especially on Windows). Make the
    # target genuinely older rather than relying on creation order among UUIDs.
    Notification.objects.filter(pk=own.pk).update(created_at=timezone.now() - timedelta(days=1))
    Notification.objects.bulk_create([
        Notification(user=user, business=business, title=f"اعلان قدیمی {index}") for index in range(55)
    ])
    first = client.get(reverse("notifications:list"), {"state": "unread"})
    assert first.context["page"].total == 58
    assert first.context["page"].has_next
    last = client.get(reverse("notifications:list"), {"state": "unread", "page": "999"})
    assert own.title in last.content.decode()
    assert not Notification.objects.filter(user=user, is_read=True).exists()


@pytest.mark.parametrize("source,index", [("stored", 3), ("reminder", 6)])
def test_marking_one_read_is_scoped_and_idempotent(client, center, source, index):
    obj = center[index]
    url = reverse("notifications:read", args=[source, obj.pk])
    assert client.get(url).status_code == 405
    for _ in range(2):
        assert client.post(url, {"state": "unread", "page": "2"}).status_code == 302
    if source == "stored":
        obj.refresh_from_db()
        assert obj.is_read
    else:
        assert FollowUpReminderRead.objects.filter(followup=obj, user=center[2]).count() == 1
    center[4].refresh_from_db()
    assert not center[4].is_read


def test_read_all_affects_only_current_business_and_account_notifications(client, center):
    _, _, user, own, foreign, general, followup = center
    url = reverse("notifications:read_all")
    assert client.get(url).status_code == 405
    assert client.post(url).status_code == 302
    assert Notification.objects.filter(pk__in=[own.pk, general.pk], is_read=True).count() == 2
    foreign.refresh_from_db()
    assert not foreign.is_read
    assert FollowUpReminderRead.objects.filter(user=user, followup=followup).exists()


def test_cannot_mark_another_business_or_users_notification(client, center):
    business, _, _, _, foreign, _, _ = center
    someone_else = Notification.objects.create(
        user=make_user("09380001004"), business=business, title="اعلان خصوصی کاربر دیگر",
    )
    for obj in (foreign, someone_else):
        assert client.post(reverse("notifications:read", args=["stored", obj.pk])).status_code == 404
        obj.refresh_from_db()
        assert not obj.is_read


def test_read_actions_require_csrf(center):
    client = Client(enforce_csrf_checks=True)
    client.force_login(center[2])
    assert client.post(reverse("notifications:read_all")).status_code == 403


def test_notification_links_cannot_run_javascript_or_leave_the_site(client, center):
    business, _, user, *_ = center
    for link in ("javascript:alert(1)", "//evil.example/", "https://evil.example/"):
        Notification.objects.create(user=user, business=business, title="پیوند نامعتبر", link=link)
    body = client.get(reverse("notifications:list")).content.decode()
    assert 'href="javascript:' not in body and 'href="//evil' not in body and 'href="https://evil' not in body


def test_large_notification_history_has_bounded_page_queries(client, center, django_assert_max_num_queries):
    business, _, user, *_ = center
    Notification.objects.bulk_create([
        Notification(user=user, business=business, title=f"اعلان {index}") for index in range(200)
    ])
    with django_assert_max_num_queries(12):
        response = client.get(reverse("notifications:list"))
    assert len(response.context["notifications"]) == 24
    assert response.context["page"].total == 203


def test_reminder_access_is_checked_again_when_marking_read(client, center):
    _, _, user, _, _, _, followup = center
    user_membership = user.memberships.get(business=center[0])
    user_membership.role, user_membership.permissions = "viewer", []
    user_membership.save()
    response = client.post(reverse("notifications:read", args=["reminder", followup.pk]))
    assert response.status_code == 404
    assert not FollowUpReminderRead.objects.filter(user=user, followup=followup).exists()
