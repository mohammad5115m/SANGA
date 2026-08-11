from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import OTPChallenge
from apps.accounts.services import OTPValidationError, request_login_otp

User = get_user_model()


@pytest.mark.django_db
def test_login_otp_http_flow(client, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"

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
    assert User.objects.filter(phone="09121234567").exists()


@pytest.mark.django_db
def test_login_page_creates_challenge(client, settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"
    response = client.post("/auth/login/", {"phone": "09123334444"})
    assert response.status_code == 302
    assert OTPChallenge.objects.filter(phone="09123334444").exists()


@pytest.mark.django_db
def test_invalid_phone_rejected():
    with pytest.raises(OTPValidationError):
        request_login_otp("123")
