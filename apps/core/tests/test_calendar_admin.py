from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.contrib import admin
from django.urls import reverse
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from apps.accounts.models import User


@pytest.fixture
def admin_client(client, db):
    user = User.objects.create_superuser(phone="09995550101", password="test-only-password")
    client.force_login(user)
    return client


def test_admin_uses_persian_rtl_and_jalali_user_dates(admin_client):
    response = admin_client.get(reverse("admin:accounts_user_changelist"), HTTP_ACCEPT_LANGUAGE="en")
    html = response.content.decode()
    assert response.status_code == 200
    assert 'lang="fa"' in html and 'dir="rtl"' in html
    assert "مدیریت سنگا" in html
    assert "jalali_date_joined" in html


def test_admin_datetime_widget_preserves_the_canonical_field(admin_client):
    user = User.objects.get(phone="09995550101")
    html = admin_client.get(reverse("admin:accounts_user_change", args=[user.pk])).content.decode()
    assert 'name="date_joined_jalali"' in html
    assert 'name="date_joined_time"' in html
    assert 'name="date_joined"' in html


def test_admin_jalali_range_includes_the_whole_local_day(admin_client):
    interval = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.DAYS)
    inside = PeriodicTask.objects.create(
        name="داخل بازه", task="example", interval=interval,
        start_time=datetime(2024, 3, 20, 19, 0, tzinfo=ZoneInfo("UTC")),
    )
    PeriodicTask.objects.create(
        name="خارج بازه", task="example", interval=interval,
        start_time=datetime(2024, 3, 20, 22, 0, tzinfo=ZoneInfo("UTC")),
    )
    response = admin_client.get(reverse("admin:django_celery_beat_periodictask_changelist"), {
        "start_time__gte_jalali": "۱۴۰۳/۰۱/۰۱",
        "start_time__lt_jalali": "۱۴۰۳/۰۱/۰۱",
    })
    assert response.status_code == 200
    assert list(response.context["cl"].queryset) == [inside]
    assert admin.site._registry[PeriodicTask].date_hierarchy is None


def test_admin_invalid_range_does_not_show_unfiltered_results(admin_client):
    response = admin_client.get(reverse("admin:django_celery_beat_periodictask_changelist"), {
        "start_time__gte_jalali": "۱۴۰۳/۱۳/۰۱",
    })
    assert response.status_code == 200
    assert not response.context["cl"].queryset.exists()
    assert "errorlist" in response.content.decode()
