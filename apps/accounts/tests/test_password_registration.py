from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from django.contrib.auth.hashers import check_password
from django.urls import reverse

from apps.accounts.models import OTPChallenge, RegistrationRequest, User
from apps.accounts.password_auth import LOGIN_REFUSED, review_registration
from apps.accounts.services import OTPError, request_customer_otp, request_login_otp, verify_login_otp
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import BusinessServiceError

PASSWORD = "Stone-Account!2026"
pytestmark = pytest.mark.django_db


def payload(**changes):
    return {
        "phone": "۰۹۱۲۳۴۵۸۷۶۹",
        "full_name": "مالک نمونه",
        "business_name": "سنگ نمونه",
        "city": "اصفهان",
        "password1": PASSWORD,
        "password2": PASSWORD,
        **changes,
    }


@pytest.fixture(autouse=True)
def password_phase(settings):
    settings.AUTH_LOGIN_METHOD = "password"
    settings.CUSTOMER_OTP_ENABLED = False
    settings.PUBLIC_REGISTRATION_ENABLED = True


@pytest.fixture
def platform_admin():
    return User.objects.create_superuser(phone="09129998001", password=PASSWORD)


def test_registration_waits_for_admin_without_creating_a_login_or_sending_sms(client):
    with patch("apps.accounts.services._send_code") as sms:
        response = client.post(reverse("accounts:register"), payload(), follow=True)
        sms.assert_not_called()
    assert response.status_code == 200
    registration = RegistrationRequest.objects.get()
    assert registration.phone == "09123458769" and registration.status == "pending"
    assert check_password(PASSWORD, registration.password_hash)
    assert PASSWORD not in response.content.decode()
    assert not User.objects.exists() and not Business.objects.exists() and not OTPChallenge.objects.exists()
    assert "_auth_user_id" not in client.session
    login = client.post(reverse("accounts:login"), {"phone": registration.phone, "password": PASSWORD})
    assert LOGIN_REFUSED in login.content.decode() and "_auth_user_id" not in client.session


def test_admin_approval_creates_exactly_one_owner_and_allows_password_login(client, platform_admin):
    client.post(reverse("accounts:register"), payload())
    registration = RegistrationRequest.objects.get()
    review_registration(registration=registration, actor=platform_admin, approve=True)
    review_registration(registration=registration, actor=platform_admin, approve=True)
    user = User.objects.get(phone=registration.phone)
    membership = BusinessMembership.objects.get(user=user)
    assert user.check_password(PASSWORD) and not user.is_staff and not user.is_superuser
    assert membership.role == "owner" and membership.business.verification_status == "verified"
    assert Business.objects.count() == 1
    registration.refresh_from_db()
    assert registration.password_hash == "" and registration.reviewed_by == platform_admin
    response = client.post(reverse("accounts:login"), {"phone": "۰۹۱۲۳۴۵۸۷۶۹", "password": PASSWORD})
    assert response.url == reverse("businesses:post_login")
    assert client.session["_auth_user_id"] == str(user.pk) and not OTPChallenge.objects.exists()


def test_rejected_request_cannot_be_approved_later_or_login(client, platform_admin):
    client.post(reverse("accounts:register"), payload())
    registration = RegistrationRequest.objects.get()
    review_registration(registration=registration, actor=platform_admin, approve=False)
    review_registration(registration=registration, actor=platform_admin, approve=True)
    registration.refresh_from_db()
    assert registration.status == "rejected" and registration.password_hash == ""
    assert not User.objects.filter(phone=registration.phone).exists()


def test_repeated_signup_never_replaces_pending_password_or_identity(client):
    client.post(reverse("accounts:register"), payload())
    original = RegistrationRequest.objects.get()
    client.post(
        reverse("accounts:register"),
        payload(
            full_name="نام دیگر",
            password1="Different-Secret!826",
            password2="Different-Secret!826",
        ),
    )
    current = RegistrationRequest.objects.get()
    assert current.full_name == original.full_name and current.password_hash == original.password_hash


def test_signup_never_overwrites_or_reactivates_an_existing_account(client):
    existing = User.objects.create_user(phone="09123458769", password=PASSWORD, is_active=False)
    client.post(
        reverse("accounts:register"), payload(password1="Different-Secret!826", password2="Different-Secret!826")
    )
    existing.refresh_from_db()
    assert existing.check_password(PASSWORD) and not existing.is_active
    assert not RegistrationRequest.objects.exists() and not Business.objects.exists()


@pytest.mark.parametrize(
    "changes,field",
    [
        ({"phone": "123"}, "phone"),
        ({"full_name": ""}, "full_name"),
        ({"business_name": ""}, "business_name"),
        ({"city": ""}, "city"),
        ({"password1": "12345678", "password2": "12345678"}, "password1"),
        ({"password2": "different"}, "password2"),
    ],
)
def test_invalid_registration_preserves_non_secret_values(client, changes, field):
    response = client.post(reverse("accounts:register"), payload(**changes))
    assert field in response.context["form"].errors
    assert not RegistrationRequest.objects.exists()
    assert PASSWORD not in response.content.decode()


@pytest.mark.parametrize("kind", ["unknown", "inactive", "unusable", "wrong"])
def test_password_login_has_uniform_refusal_and_never_creates_accounts(client, kind):
    if kind != "unknown":
        User.objects.create_user(
            phone="09123458769", password=None if kind == "unusable" else PASSWORD, is_active=kind != "inactive"
        )
    response = client.post(
        reverse("accounts:login"), {"phone": "09123458769", "password": "wrong" if kind == "wrong" else PASSWORD}
    )
    assert LOGIN_REFUSED in response.content.decode()
    assert "_auth_user_id" not in client.session and not Business.objects.exists()


def test_password_login_cannot_be_bypassed_by_posting_only_phone(client):
    User.objects.create_user(phone="09123458769", password=PASSWORD)
    response = client.post(reverse("accounts:login"), {"phone": "09123458769"})
    assert "password" in response.context["form"].errors and "_auth_user_id" not in client.session


def test_platform_owner_uses_same_password_login_and_has_reset_control(client, platform_admin):
    response = client.post(reverse("accounts:login"), {"phone": platform_admin.phone, "password": PASSWORD})
    assert response.url == reverse("admin:index")
    user = User.objects.create_user(phone="09123458769")
    detail = client.get(reverse("admin:accounts_user_change", args=[user.pk]))
    assert detail.status_code == 200 and "password/" in detail.content.decode()
    reset = client.post(
        reverse("admin:auth_user_password_change", args=[user.pk]),
        {
            "password1": PASSWORD,
            "password2": PASSWORD,
            "set_usable_password": "true",
        },
    )
    assert reset.status_code == 302
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_old_password_is_required_to_change_password_and_session_is_preserved(client):
    user = User.objects.create_user(phone="09123458769", password=PASSWORD)
    client.force_login(user)
    url = reverse("accounts:password_change")
    data = {"old_password": "wrong", "new_password1": "Replacement!Stone2026", "new_password2": "Replacement!Stone2026"}
    assert "old_password" in client.post(url, data).context["form"].errors
    data["old_password"] = PASSWORD
    assert client.post(url, data).status_code == 302
    assert client.session["_auth_user_id"] == str(user.pk)
    user.refresh_from_db()
    assert user.check_password(data["new_password1"])


def test_non_admin_cannot_approve_and_approval_rolls_back_on_business_failure(client, platform_admin):
    client.post(reverse("accounts:register"), payload())
    registration = RegistrationRequest.objects.get()
    member = User.objects.create_user(phone="09129998002", is_staff=True)
    with pytest.raises(OTPError, match="مدیر پلتفرم"):
        review_registration(registration=registration, actor=member, approve=True)
    with patch("apps.businesses.services.create_business_for_owner", side_effect=BusinessServiceError("failed")):
        with pytest.raises(BusinessServiceError):
            review_registration(registration=registration, actor=platform_admin, approve=True)
    registration.refresh_from_db()
    assert registration.status == "pending" and check_password(PASSWORD, registration.password_hash)
    assert not User.objects.filter(phone=registration.phone).exists() and not Business.objects.exists()


def test_admin_can_approve_from_queue_but_normal_user_cannot(client, platform_admin):
    client.post(reverse("accounts:register"), payload())
    registration = RegistrationRequest.objects.get()
    client.force_login(platform_admin)
    index = client.get(reverse("admin:index"))
    assert "درخواست ثبت‌نام در انتظار بررسی" in index.content.decode()
    url = reverse("admin:accounts_registrationrequest_changelist")
    response = client.post(url, {"action": "approve_requests", "_selected_action": str(registration.pk)})
    assert response.status_code == 302
    registration.refresh_from_db()
    assert registration.status == "approved"
    client.force_login(User.objects.get(phone=registration.phone))
    assert client.post(url, {"action": "reject_requests", "_selected_action": str(registration.pk)}).status_code == 302
    registration.refresh_from_db()
    assert registration.status == "approved"


def test_disabled_otp_services_and_stale_verify_url_cannot_authenticate_or_send_sms(client):
    User.objects.create_user(phone="09121111111", password=PASSWORD, is_active=False)
    with patch("apps.accounts.services._send_code") as sms:
        for function in (request_login_otp, request_customer_otp):
            with pytest.raises(OTPError):
                function("09121111111")
        response = client.post(reverse("accounts:verify"), {"phone": "09121111111", "code": "123456"})
        assert response.url == reverse("accounts:login")
        with pytest.raises(OTPError):
            verify_login_otp("09121111111", "123456", request=response.wsgi_request)
        sms.assert_not_called()
    assert not OTPChallenge.objects.exists() and not User.objects.get(phone="09121111111").is_active


def test_password_and_registration_requests_are_rate_limited(client, settings):
    settings.AUTH_MAX_ATTEMPTS_PER_HOUR = 2
    for _ in range(2):
        client.post(reverse("accounts:login"), {"phone": "09123458769", "password": PASSWORD})
    response = client.post(reverse("accounts:login"), {"phone": "09123458769", "password": PASSWORD})
    assert "بیش از حد" in response.content.decode()
    # A password budget does not consume a registration budget.
    assert client.post(reverse("accounts:register"), payload()).status_code == 302


def test_registration_switch_is_enforced_on_the_server(client, settings):
    settings.PUBLIC_REGISTRATION_ENABLED = False
    assert client.post(reverse("accounts:register"), payload()).url == reverse("accounts:login")
    assert not RegistrationRequest.objects.exists()


def test_rejected_applicant_can_submit_corrected_details_for_a_new_review(client, platform_admin):
    client.post(reverse("accounts:register"), payload())
    registration = RegistrationRequest.objects.get()
    review_registration(registration=registration, actor=platform_admin, approve=False)
    client.post(reverse("accounts:register"), payload(business_name="نام اصلاح‌شده"))
    registration.refresh_from_db()
    assert registration.status == "pending" and registration.business_name == "نام اصلاح‌شده"
    assert registration.reviewed_at is None and registration.reviewed_by is None
    assert check_password(PASSWORD, registration.password_hash)
    assert not User.objects.filter(phone=registration.phone).exists()


def test_signup_cannot_request_privileges_and_admin_cannot_rewrite_application(client, platform_admin):
    client.post(reverse("accounts:register"), payload(is_superuser="true", role="manager", status="approved"))
    registration = RegistrationRequest.objects.get()
    assert registration.status == "pending"
    client.force_login(platform_admin)
    response = client.post(reverse("admin:accounts_registrationrequest_change", args=[registration.pk]), {
        "status": "approved", "phone": platform_admin.phone,
    })
    assert response.status_code == 403
    review_registration(registration=registration, actor=platform_admin, approve=True)
    owner = User.objects.get(phone=registration.phone)
    assert not owner.is_superuser and not owner.is_staff and owner.memberships.get().role == "owner"


def test_authentication_writes_require_csrf():
    from django.test import Client

    client = Client(enforce_csrf_checks=True)
    assert client.post(reverse("accounts:register"), payload()).status_code == 403
    assert client.post(reverse("accounts:login"), {"phone": "09123458769", "password": PASSWORD}).status_code == 403
    assert not RegistrationRequest.objects.exists()


def test_rotating_phone_numbers_does_not_bypass_address_budget(client, settings):
    settings.AUTH_MAX_ATTEMPTS_PER_IP_PER_HOUR = 2
    for phone in ("09129997101", "09129997102"):
        client.post(reverse("accounts:login"), {"phone": phone, "password": PASSWORD})
    response = client.post(reverse("accounts:login"), {"phone": "09129997103", "password": PASSWORD})
    assert "بیش از حد" in response.content.decode()


@pytest.mark.parametrize("enabled", [False, True])
def test_production_needs_sms_configuration_only_when_otp_is_enabled(tmp_path, enabled):
    env = dict(
        os.environ,
        DJANGO_DATABASE="sqlite",
        DJANGO_SECRET_KEY="test-only-" * 8,
        DJANGO_ALLOWED_HOSTS="example.test",
        USE_S3="false",
        SANGA_ALLOW_LOCAL_MEDIA="true",
        SANGA_MEDIA_ROOT=str(tmp_path),
        AUTH_LOGIN_METHOD="password",
        CUSTOMER_OTP_ENABLED=str(enabled),
        SMS_PROVIDER="console",
        SMS_ALLOW_UNDELIVERED="false",
    )
    result = subprocess.run(
        [sys.executable, "-c", "import config.settings.production"], env=env, capture_output=True, text=True, timeout=30
    )
    assert (result.returncode == 0) is not enabled, result.stderr
