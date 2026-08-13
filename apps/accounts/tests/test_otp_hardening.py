"""One-time means once, and a limit that only counts phone numbers is not a limit.

Verification was read-check-write with no lock. Two requests carrying the same
correct code both read ``is_used=False``, both concluded they had won, and a code
that is one-time by definition was used twice. Wrong guesses had the mirror
problem: concurrent attempts read the same counter and wrote back the same
increment, so the attempt limit could be walked past by guessing in parallel.

And every rate limit keyed on the phone number, so a caller working through a
list of numbers never touched one — SANGA paying the gateway for each, and each
recipient getting an unexplained message.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.accounts.models import OTPChallenge
from apps.accounts.services import (
    OTPRateLimitError,
    OTPValidationError,
    request_customer_otp,
    verify_customer_otp,
)


@pytest.fixture(autouse=True)
def console_sms(settings):
    settings.DEBUG = True
    settings.SMS_PROVIDER = "console"


def _code(phone: str) -> str:
    """Recover the plaintext of the latest challenge, as the console flow does."""
    from apps.accounts.services import _hash_code

    challenge = OTPChallenge.objects.filter(phone=phone).order_by("-created_at").first()
    for candidate in range(1000000):
        code = f"{candidate:06d}"
        if _hash_code(code) == challenge.code_hash:
            return code
    raise AssertionError("code not found")


# --- one-time means once ---------------------------------------------------------


@pytest.mark.django_db
def test_a_code_cannot_be_verified_twice(rf):
    request_customer_otp("09121230001", request=rf.post("/"))
    code = _code("09121230001")

    assert verify_customer_otp("09121230001", code) is True
    with pytest.raises(OTPValidationError):
        verify_customer_otp("09121230001", code)


@pytest.mark.django_db
def test_a_wrong_guess_burns_an_attempt(rf, settings):
    settings.OTP_MAX_ATTEMPTS = 3
    request_customer_otp("09121230002", request=rf.post("/"))

    for _ in range(3):
        with pytest.raises(OTPValidationError):
            verify_customer_otp("09121230002", "000000")

    challenge = OTPChallenge.objects.get(phone="09121230002")
    assert challenge.attempts == 3

    # Exhausted: even the right code is refused now.
    with pytest.raises(OTPValidationError):
        verify_customer_otp("09121230002", _code("09121230002"))


@pytest.mark.django_db
def test_an_expired_code_is_refused(rf, settings):
    settings.OTP_EXPIRY_SECONDS = -1
    request_customer_otp("09121230003", request=rf.post("/"))
    with pytest.raises(OTPValidationError):
        verify_customer_otp("09121230003", _code("09121230003"))


# --- rate limits -----------------------------------------------------------------


@pytest.mark.django_db
def test_one_address_cannot_message_an_unlimited_number_of_phones(rf, settings):
    """The limit that was missing. Every other one keys on the phone number."""
    settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR = 3
    request = rf.post("/", REMOTE_ADDR="203.0.113.5")

    for index in range(3):
        request_customer_otp(f"0912999000{index}", request=request)

    with pytest.raises(OTPRateLimitError):
        request_customer_otp("09129990009", request=request)


@pytest.mark.django_db
def test_a_different_address_is_not_caught_by_someone_elses_limit(rf, settings):
    settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR = 2
    noisy = rf.post("/", REMOTE_ADDR="203.0.113.5")
    quiet = rf.post("/", REMOTE_ADDR="203.0.113.9")

    request_customer_otp("09129991001", request=noisy)
    request_customer_otp("09129991002", request=noisy)
    with pytest.raises(OTPRateLimitError):
        request_customer_otp("09129991003", request=noisy)

    # A different visitor is unaffected.
    request_customer_otp("09129991004", request=quiet)


@pytest.mark.django_db
def test_the_per_phone_cooldown_still_applies(rf):
    request_customer_otp("09121230010", request=rf.post("/"))
    with pytest.raises(OTPRateLimitError):
        request_customer_otp("09121230010", request=rf.post("/"))


@pytest.mark.django_db
def test_a_challenge_row_is_written_for_an_unknown_phone_too(rf):
    """Otherwise the rate limiter itself answers "is this number a SANGA
    account?" by behaving differently."""
    from apps.accounts.services import request_login_otp

    request_login_otp("09129998888", request=rf.post("/"))
    assert OTPChallenge.objects.filter(phone="09129998888").exists()


# --- provider configuration -------------------------------------------------------


@pytest.mark.django_db
def test_an_unknown_provider_is_refused_rather_than_guessed(settings):
    """It used to fall back to the console provider with a warning, so a typo
    produced a deployment that sent nothing and logged every code."""
    from apps.accounts.sms import get_sms_provider

    settings.SMS_PROVIDER = "kavenegarr"
    with pytest.raises(ImproperlyConfigured):
        get_sms_provider()


@pytest.mark.django_db
def test_the_console_provider_is_marked_as_not_delivering(settings):
    """Which is what production settings check before agreeing to start."""
    from apps.accounts.sms import PROVIDERS

    assert PROVIDERS["console"].delivers is False
    assert PROVIDERS["null"].delivers is False
