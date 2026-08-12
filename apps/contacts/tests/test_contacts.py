from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import create_business_for_owner
from apps.contacts.models import Contact
from apps.contacts.selectors import contacts_for_business, get_contact, linkable_businesses
from apps.contacts.services import (
    ContactError,
    archive_contact,
    create_contact,
    restore_contact,
    update_contact,
)

User = get_user_model()


@pytest.fixture
def setup(db):
    owner_a = User.objects.create_user(phone="09120000101", full_name="مالک الف")
    owner_b = User.objects.create_user(phone="09120000102", full_name="مالک ب")
    viewer_user = User.objects.create_user(phone="09120000103", full_name="بازدیدکننده")

    biz_a = create_business_for_owner(owner=owner_a, name="سنگ الف", city="محلات")
    biz_b = create_business_for_owner(owner=owner_b, name="سنگ ب", city="تهران")

    m_a = BusinessMembership.objects.get(user=owner_a, business=biz_a)
    m_b = BusinessMembership.objects.get(user=owner_b, business=biz_b)
    viewer_m = BusinessMembership.objects.create(
        user=viewer_user,
        business=biz_a,
        role=BusinessMembership.Role.VIEWER,
        status=BusinessMembership.Status.ACTIVE,
    )
    return {
        "owner_a": owner_a,
        "owner_b": owner_b,
        "biz_a": biz_a,
        "biz_b": biz_b,
        "m_a": m_a,
        "m_b": m_b,
        "viewer_m": viewer_m,
    }


def test_create_contact_success(setup):
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="آقای رضایی",
        phone="09121110000",
    )
    assert contact.pk is not None
    assert contact.business_id == setup["biz_a"].id
    assert contact.created_by_id == setup["owner_a"].id
    assert contact.is_active is True


def test_create_needs_nothing_beyond_a_name(setup):
    """Every contact is a همکار: there is no relationship type to choose."""
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="همکار بی‌نوع",
    )
    assert contact.pk is not None


def test_create_requires_name(setup):
    with pytest.raises(ContactError):
        create_contact(
            business=setup["biz_a"],
            membership=setup["m_a"],
            display_name="ا",
        )


def test_viewer_without_capability_cannot_create(setup):
    with pytest.raises(ContactError):
        create_contact(
            business=setup["biz_a"],
            membership=setup["viewer_m"],
            display_name="مخاطب",
        )


def test_tenant_isolation_selector(setup):
    create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="مال الف",
    )
    create_contact(
        business=setup["biz_b"],
        membership=setup["m_b"],
        display_name="مال ب",
    )
    a_names = [c.display_name for c in contacts_for_business(setup["biz_a"])]
    b_names = [c.display_name for c in contacts_for_business(setup["biz_b"])]
    assert a_names == ["مال الف"]
    assert b_names == ["مال ب"]


def test_get_contact_rejects_cross_business(setup):
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="خصوصی الف",
    )
    with pytest.raises(Contact.DoesNotExist):
        get_contact(setup["biz_b"], contact.id)


def test_free_text_search(setup):
    create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="سنگ آریا",
        phone="09120001111",
    )
    create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="مشتری خرده",
        phone="09122223333",
    )
    by_name = list(contacts_for_business(setup["biz_a"], q="آریا"))
    assert len(by_name) == 1 and by_name[0].display_name == "سنگ آریا"

    by_phone = list(contacts_for_business(setup["biz_a"], q="2222"))
    assert len(by_phone) == 1 and by_phone[0].display_name == "مشتری خرده"


def test_link_to_any_colleague_needs_no_approval(setup):
    """No partnership of any kind exists between these two businesses."""
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="همکار متصل",
        linked_business=setup["biz_b"],
    )
    assert contact.linked_business_id == setup["biz_b"].id


def test_link_to_a_suspended_business_is_refused(setup):
    suspended = setup["biz_b"]
    suspended.status = Business.Status.SUSPENDED
    suspended.save(update_fields=["status"])

    with pytest.raises(ContactError):
        create_contact(
            business=setup["biz_a"],
            membership=setup["m_a"],
            display_name="کسب‌وکار معلق",
            linked_business=suspended,
        )


def test_linkable_businesses_lists_colleagues_but_never_self(setup):
    options = list(linkable_businesses(setup["biz_a"]))
    assert options == [setup["biz_b"]]


def test_second_contact_cannot_link_the_same_partner(setup):
    first = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="همکار متصل",
        linked_business=setup["biz_b"],
    )
    with pytest.raises(ContactError) as exc_info:
        create_contact(
            business=setup["biz_a"],
            membership=setup["m_a"],
            display_name="همان همکار، مخاطب دوم",
            linked_business=setup["biz_b"],
        )
    assert first.display_name in exc_info.value.message
    assert Contact.objects.filter(business=setup["biz_a"], linked_business=setup["biz_b"]).count() == 1


def test_update_cannot_move_a_partner_link_onto_a_second_contact(setup):
    create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="همکار متصل",
        linked_business=setup["biz_b"],
    )
    other = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="مخاطب دیگر",
    )
    with pytest.raises(ContactError):
        update_contact(
            contact=other,
            membership=setup["m_a"],
            display_name="مخاطب دیگر",
            linked_business=setup["biz_b"],
        )
    other.refresh_from_db()
    assert other.linked_business_id is None


def test_editing_the_linked_contact_itself_keeps_its_own_link(setup):
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="همکار متصل",
        linked_business=setup["biz_b"],
    )
    update_contact(
        contact=contact,
        membership=setup["m_a"],
        display_name="همکار متصل (ویرایش‌شده)",
        linked_business=setup["biz_b"],
    )
    contact.refresh_from_db()
    assert contact.display_name == "همکار متصل (ویرایش‌شده)"
    assert contact.linked_business_id == setup["biz_b"].id


def test_db_constraint_rejects_a_duplicate_partner_link(setup):
    create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="همکار متصل",
        linked_business=setup["biz_b"],
    )
    # Bypass the service entirely: the database itself must refuse.
    with pytest.raises(IntegrityError), transaction.atomic():
        Contact.objects.create(
            business=setup["biz_a"],
            display_name="دور زدن سرویس",
            linked_business=setup["biz_b"],
        )


def test_two_businesses_may_each_link_the_same_partner(setup):
    create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="ب از نگاه الف",
        linked_business=setup["biz_b"],
    )
    # The constraint is per owning business, not global.
    contact_b = create_contact(
        business=setup["biz_b"],
        membership=setup["m_b"],
        display_name="الف از نگاه ب",
        linked_business=setup["biz_a"],
    )
    assert contact_b.linked_business_id == setup["biz_a"].id


def test_cannot_link_self(setup):
    with pytest.raises(ContactError):
        create_contact(
            business=setup["biz_a"],
            membership=setup["m_a"],
            display_name="خودم",
            linked_business=setup["biz_a"],
        )


def test_update_contact_cross_business_blocked(setup):
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="الف",
    )
    with pytest.raises(ContactError):
        update_contact(
            contact=contact,
            membership=setup["m_b"],
            display_name="نفوذ",
        )


def test_archive_hides_from_default_list(setup):
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="بایگانی‌شونده",
    )
    archive_contact(contact=contact, membership=setup["m_a"])
    assert list(contacts_for_business(setup["biz_a"])) == []
    assert len(list(contacts_for_business(setup["biz_a"], include_archived=True))) == 1


def test_view_tenant_isolation_returns_404(client, setup):
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name="خصوصی الف",
    )
    client.force_login(setup["owner_b"])
    resp = client.get(f"/app/contacts/{contact.id}/")
    assert resp.status_code == 404


def test_view_list_requires_capability(client, setup):
    viewer = setup["viewer_m"].user
    client.force_login(viewer)
    resp = client.get("/app/contacts/")
    assert resp.status_code == 302


def test_navigation_offers_the_colleague_directory_and_gates_the_ledger(client, setup):
    """Contacts left the navigation; the Business directory replaced them.

    The directory needs no capability — a colleague is a Business, and every
    member can see who else is on the platform. The ledger still stops at
    ``ledger.view``.
    """
    client.force_login(setup["owner_a"])
    owner_dashboard = client.get("/app/")
    assert owner_dashboard.status_code == 200
    owner_content = owner_dashboard.content.decode()
    assert "/app/contacts/" not in owner_content
    assert "/app/colleagues/" in owner_content

    client.force_login(setup["viewer_m"].user)
    viewer_dashboard = client.get("/app/")
    assert viewer_dashboard.status_code == 200
    viewer_content = viewer_dashboard.content.decode()
    assert "/app/colleagues/" in viewer_content
    assert "/app/accounting/" not in viewer_content


def _archived_contact(setup, name="بایگانی‌شده"):
    contact = create_contact(
        business=setup["biz_a"],
        membership=setup["m_a"],
        display_name=name,
    )
    archive_contact(contact=contact, membership=setup["m_a"])
    return contact


def test_archived_contact_is_reachable_through_the_archived_filter(client, setup):
    contact = _archived_contact(setup, name="سنگ بایگانی")
    client.force_login(setup["owner_a"])

    default_list = client.get("/app/contacts/")
    assert contact.display_name not in default_list.content.decode()

    archived_list = client.get("/app/contacts/", {"archived": "1"})
    assert contact.display_name in archived_list.content.decode()


def test_restore_returns_contact_to_the_active_list(client, setup):
    contact = _archived_contact(setup)
    client.force_login(setup["owner_a"])

    resp = client.post(f"/app/contacts/{contact.id}/restore/")
    assert resp.status_code == 302

    contact.refresh_from_db()
    assert contact.is_active is True
    assert list(contacts_for_business(setup["biz_a"])) == [contact]


def test_restore_rejects_get(client, setup):
    contact = _archived_contact(setup)
    client.force_login(setup["owner_a"])

    resp = client.get(f"/app/contacts/{contact.id}/restore/")
    assert resp.status_code == 405

    contact.refresh_from_db()
    assert contact.is_active is False


def test_restore_is_tenant_scoped(client, setup):
    contact = _archived_contact(setup)
    client.force_login(setup["owner_b"])

    resp = client.post(f"/app/contacts/{contact.id}/restore/")
    assert resp.status_code == 404

    contact.refresh_from_db()
    assert contact.is_active is False


def test_restore_requires_capability(client, setup):
    contact = _archived_contact(setup)
    client.force_login(setup["viewer_m"].user)

    resp = client.post(f"/app/contacts/{contact.id}/restore/")
    assert resp.status_code == 302

    contact.refresh_from_db()
    assert contact.is_active is False


def test_restore_service_refuses_without_capability(setup):
    contact = _archived_contact(setup)
    with pytest.raises(ContactError):
        restore_contact(contact=contact, membership=setup["viewer_m"])

    contact.refresh_from_db()
    assert contact.is_active is False
