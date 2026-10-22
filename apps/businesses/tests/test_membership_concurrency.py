"""Every supported seat allocator must serialize on the business row."""

import threading

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections

from apps.accounts.models import User
from apps.businesses.models import BusinessMembership
from apps.businesses.services import BusinessServiceError, invite_member, set_membership_status
from apps.core.testing import make_business, owner_membership


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("other_path", ["reactivate", "invite", "command"])
def test_two_allocators_cannot_take_the_last_seat(other_path):
    business = make_business(name="سنگ ظرفیت همزمان", owner_phone="09129996001")
    business.seat_limit = 2
    business.save(update_fields=["seat_limit"])
    admin = User.objects.create_superuser(phone="09129996002", password="test-only")
    first = BusinessMembership.objects.create(
        business=business, user=User.objects.create_user(phone="09129996003"), status="suspended",
    )
    second_user = User.objects.create_user(phone="09129996004")
    second = None
    if other_path != "command":
        second = BusinessMembership.objects.create(business=business, user=second_user, status="suspended")
    owner = owner_membership(business)
    barrier = threading.Barrier(2, timeout=15)
    results = []
    errors = []

    def allocate(index):
        close_old_connections()
        try:
            barrier.wait()
            if index == 0:
                set_membership_status(membership=first, status="active", actor=admin)
            elif other_path == "reactivate":
                set_membership_status(membership=second, status="active", actor=admin)
            elif other_path == "invite":
                invite_member(business=business, actor_membership=owner, user=second_user)
            else:
                call_command("provision_user", phone=second_user.phone, business=business.slug)
            results.append("allocated")
        except (BusinessServiceError, CommandError) as exc:
            errors.append(str(exc))
        finally:
            close_old_connections()

    threads = [threading.Thread(target=allocate, args=(index,), daemon=True) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "seat allocation deadlocked"
    assert results == ["allocated"]
    assert len(errors) == 1 and "سقف کاربران" in errors[0]
    assert business.memberships.filter(status="active").count() == 2
