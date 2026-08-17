from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Business, BusinessMembership


@pytest.mark.django_db
def test_seed_demo_repairs_disabled_demo_login():
    call_command("seed_demo")

    user = User.objects.get(phone="09121111111")
    membership = BusinessMembership.objects.get(
        user=user,
        role=BusinessMembership.Role.OWNER,
    )
    business = membership.business

    user.is_active = False
    user.save(update_fields=["is_active"])
    membership.status = BusinessMembership.Status.SUSPENDED
    membership.save(update_fields=["status"])
    business.status = Business.Status.SUSPENDED
    business.verification_status = Business.VerificationStatus.SUSPENDED
    business.active_until = timezone.localdate() - timedelta(days=1)
    business.save(
        update_fields=[
            "status",
            "verification_status",
            "active_until",
            "updated_at",
        ]
    )

    call_command("seed_demo")

    user.refresh_from_db()
    membership.refresh_from_db()
    business.refresh_from_db()
    assert user.is_active is True
    assert membership.status == BusinessMembership.Status.ACTIVE
    assert business.status == Business.Status.ACTIVE
    assert business.verification_status == Business.VerificationStatus.VERIFIED
    assert business.active_until is None
