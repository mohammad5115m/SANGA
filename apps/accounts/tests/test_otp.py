from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import OTPChallenge
from apps.accounts.services import (
    OTPValidationError,
    request_login_otp,
    verify_login_otp,
)

User = get_user_model()


def _provision(phone: str, **extra) -> User:
    """Stand in for Platform Admin provisioning.

    Every login test needs this now: authentication never creates accounts, so a
    User has to exist before an OTP can succeed.
    """
    return User.objects.create_user(phone=phone, **extra)


@pytest.mark.django_db
def test_login_otp_http_flow(client, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    _provision("09121234567")

    result = request_login_otp("09121234567")
    assert result.dev_code
    assert OTPChallenge.objects.filter(phone="09121234567").exists()

    session = client.session
    session["otp_phone"] = "09121234567"
    session.save()

    response = client.post(
        "/auth/verify/",
        {"phone": "09121234567", "code": result.dev_code},
        follow=True,
    )
    assert response.status_code == 200
    assert response.wsgi_request.user.is_authenticated


def test_normalize_phone_accepts_persian_digits():
    from apps.core.persian import normalize_phone

    assert normalize_phone("۰۹۱۲۱۲۳۴۵۶۷") == "09121234567"
    assert normalize_phone("٠٩١٢١٢٣٤٥٦٧") == "09121234567"
    assert normalize_phone("+98 912 123 4567") == "09121234567"


@pytest.mark.django_db
def test_verify_accepts_persian_digit_code(client, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    _provision("09125556677")

    result = request_login_otp("۰۹۱۲۵۵۵۶۶۷۷")
    assert result.phone == "09125556677"
    persian_code = result.dev_code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

    session = client.session
    session["otp_phone"] = "09125556677"
    session.save()

    response = client.post(
        "/auth/verify/",
        {"phone": "09125556677", "code": persian_code},
        follow=True,
    )
    assert response.status_code == 200
    assert response.wsgi_request.user.is_authenticated


@pytest.mark.django_db
def test_login_page_creates_challenge(client, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    _provision("09123334444")
    response = client.post("/auth/login/", {"phone": "09123334444"})
    assert response.status_code == 302
    assert OTPChallenge.objects.filter(phone="09123334444").exists()


@pytest.mark.django_db
def test_console_login_code_is_shown_on_verification_page(client, settings, monkeypatch):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    _provision("09123334445")
    monkeypatch.setattr("apps.accounts.services.secrets.choice", lambda _digits: "7")

    response = client.post("/auth/login/", {"phone": "09123334445"}, follow=True)

    assert response.status_code == 200
    assert response.request["PATH_INFO"] == "/auth/verify/"
    assert "777777" in response.content.decode()


@pytest.mark.django_db
def test_login_code_is_never_shown_without_debug(client, settings, monkeypatch):
    settings.DEBUG = False
    settings.SMS_PROVIDER = "console"
    _provision("09123334446")
    monkeypatch.setattr("apps.accounts.services.secrets.choice", lambda _digits: "8")

    response = client.post("/auth/login/", {"phone": "09123334446"}, follow=True)

    assert response.status_code == 200
    assert "888888" not in response.content.decode()


@pytest.mark.django_db
def test_invalid_phone_rejected():
    with pytest.raises(OTPValidationError):
        request_login_otp("123")


# --- Platform provisioning boundary (P0) ------------------------------------


@pytest.mark.django_db
def test_requesting_otp_for_unknown_phone_creates_no_user():
    request_login_otp("09120000001")
    assert not User.objects.filter(phone="09120000001").exists()


@pytest.mark.django_db
def test_unknown_phone_receives_no_sms_but_still_burns_rate_limit(settings, monkeypatch):
    """Unknown numbers must consume the same quota as known ones.

    If unprovisioned phones skipped the challenge row, the rate limiter itself
    would answer "does this number have a SANGA account?".
    """
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    sent: list[str] = []
    monkeypatch.setattr(
        "apps.accounts.services.get_sms_provider",
        lambda: type("P", (), {"send": staticmethod(lambda message: sent.append(message.phone))})(),
    )

    result = request_login_otp("09120000002")

    assert sent == []
    assert result.dev_code is None
    assert OTPChallenge.objects.filter(phone="09120000002").count() == 1


@pytest.mark.django_db
def test_verifying_otp_for_unknown_phone_creates_no_user(rf, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    request_login_otp("09120000003")
    challenge = OTPChallenge.objects.get(phone="09120000003")
    # The code is only ever stored hashed, so drive verification through a known
    # plaintext by rewriting the hash the same way the service does.
    from apps.accounts.services import _hash_code

    challenge.code_hash = _hash_code("123456")
    challenge.save(update_fields=["code_hash"])

    request = rf.post("/auth/verify/")
    with pytest.raises(OTPValidationError):
        verify_login_otp("09120000003", "123456", request=request)

    assert not User.objects.filter(phone="09120000003").exists()


@pytest.mark.django_db
def test_correct_code_for_unknown_phone_cannot_be_replayed_after_provisioning(rf, settings):
    """The challenge is burned even when the login is refused."""
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

    request_login_otp("09120000004")
    challenge = OTPChallenge.objects.get(phone="09120000004")
    from apps.accounts.services import _hash_code

    challenge.code_hash = _hash_code("654321")
    challenge.save(update_fields=["code_hash"])

    with pytest.raises(OTPValidationError):
        verify_login_otp("09120000004", "654321", request=rf.post("/auth/verify/"))

    challenge.refresh_from_db()
    assert challenge.is_used is True

    # Now provision the account; the previously-correct code must be dead.
    _provision("09120000004")
    with pytest.raises(OTPValidationError):
        verify_login_otp("09120000004", "654321", request=rf.post("/auth/verify/"))


@pytest.mark.django_db
def test_unknown_and_inactive_phones_give_the_same_message(rf, settings):
    """Distinct wording would confirm whether a number belongs to an account."""
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    from apps.accounts.services import NOT_PROVISIONED_MESSAGE, _hash_code

    inactive = _provision("09120000005")
    inactive.is_active = False
    inactive.save(update_fields=["is_active"])

    messages = []
    for phone in ("09120000006", "09120000005"):
        request_login_otp(phone)
        challenge = OTPChallenge.objects.filter(phone=phone).order_by("-created_at").first()
        challenge.code_hash = _hash_code("111111")
        challenge.save(update_fields=["code_hash"])
        with pytest.raises(OTPValidationError) as exc:
            verify_login_otp(phone, "111111", request=rf.post("/auth/verify/"))
        messages.append(exc.value.message)

    assert messages[0] == messages[1] == NOT_PROVISIONED_MESSAGE


@pytest.mark.django_db
def test_provisioned_user_can_authenticate(client, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    _provision("09120000007", full_name="کاربر ثبت‌شده")

    result = request_login_otp("09120000007")
    session = client.session
    session["otp_phone"] = "09120000007"
    session.save()

    response = client.post(
        "/auth/verify/",
        {"phone": "09120000007", "code": result.dev_code},
        follow=True,
    )
    assert response.wsgi_request.user.is_authenticated
    assert response.wsgi_request.user.phone == "09120000007"
