"""Approval must never create two accounts/businesses for one application."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth.hashers import make_password
from django.db import connection

from apps.accounts.models import RegistrationRequest, User
from apps.accounts.password_auth import review_registration
from apps.businesses.models import Business, BusinessMembership

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("second_approval", [True, False])
def test_simultaneous_registration_reviews_are_atomic_and_idempotent(second_approval):
    admin = User.objects.create_superuser(phone="09120004801", password="Admin!Stone2026")
    registration = RegistrationRequest.objects.create(
        phone="09120004802", full_name="مالک آزمایش", business_name="سنگ همزمان", city="اصفهان",
        password_hash=make_password("Owner!Stone2026"),
    )
    barrier = threading.Barrier(2, timeout=15)

    def review(approve):
        try:
            barrier.wait()
            review_registration(registration=registration, actor=admin, approve=approve)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(review, approve) for approve in (True, second_approval)]
        for future in futures:
            future.result(timeout=30)
    registration.refresh_from_db()
    count = 1 if registration.status == "approved" else 0
    assert Business.objects.count() == BusinessMembership.objects.count() == count
    assert User.objects.filter(phone=registration.phone).count() == count
    if count:
        assert User.objects.get(phone=registration.phone).check_password("Owner!Stone2026")
    assert registration.password_hash == "" and registration.reviewed_by == admin
