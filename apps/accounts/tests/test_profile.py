import pytest
from django.urls import reverse

from apps.core.testing import make_business, owner_membership

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("htmx", [False, True])
def test_invalid_name_is_preserved_with_an_actionable_error(client, htmx):
    business = make_business(name="پروفایل آزمون", owner_phone="09380002001")
    user = owner_membership(business).user
    user.full_name = "نام پیشین"
    user.save()
    client.force_login(user)
    name = "نام طولانی " * 20
    response = client.post(reverse("accounts:profile"), {"full_name": name},
                           **({"HTTP_HX_REQUEST": "true"} if htmx else {}))
    user.refresh_from_db()
    assert response.status_code == 200
    assert user.full_name == "نام پیشین"
    assert name.strip() in response.content.decode()
    assert 'aria-invalid="true"' in response.content.decode()


def test_profile_updates_only_the_current_users_name(client):
    business = make_business(name="پروفایل امن", owner_phone="09380002002")
    user = owner_membership(business).user
    client.force_login(user)
    response = client.post(reverse("accounts:profile"), {
        "full_name": "  نام تازه  ", "phone": "09389999999", "is_superuser": "true",
    })
    user.refresh_from_db()
    assert response.status_code == 302
    assert user.full_name == "نام تازه" and user.phone == "09380002002" and not user.is_superuser


def test_profile_shows_current_membership_without_exposing_administration(client):
    business = make_business(name="کسب‌وکار پروفایل", owner_phone="09380002003")
    client.force_login(owner_membership(business).user)
    body = client.get(reverse("accounts:profile")).content.decode()
    assert business.name in body
    assert "شماره موبایل برای ورود" in body
    assert "مدیریت پلتفرم" not in body


@pytest.mark.parametrize("destination", ["invalid-id", "00000000-0000-0000-0000-000000000000"])
def test_switch_from_profile_refuses_invalid_or_unowned_business(client, destination):
    business = make_business(name="تعویض امن", owner_phone="09380002004")
    client.force_login(owner_membership(business).user)
    session = client.session
    session["current_business_id"] = str(business.pk)
    session.save()
    client.get(reverse("accounts:profile"))
    before = client.session["current_business_id"]
    response = client.post(reverse("businesses:switch_business"), {"business_id": destination})
    assert response.status_code == 302
    assert client.session["current_business_id"] == before
