"""OTP request limits, and the address they are counted against.

Two separate defects lived here. The limits were three counts followed by an
insert in a different transaction, so parallel requests all passed. And the
address they counted against was the leftmost ``X-Forwarded-For`` value, which
is written by whoever sent the request — so a caller could hand themselves a
fresh rate-limit key per request, or hand their traffic to somebody else's
address and get that person throttled instead.

The race lives in ``test_otp_concurrency.py``; SQLite cannot show it. This file
pins the sequential behaviour and the whole of the address logic, which needs no
concurrency to be wrong.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.models import OTPChallenge, OTPRequestThrottle
from apps.accounts.services import (
    OTPRateLimitError,
    _client_ip,
    request_customer_otp,
    request_login_otp,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def console_provider(settings):
    settings.SMS_PROVIDER = "console"


def _request(*, remote: str = "203.0.113.9", forwarded: str | None = None):
    factory = RequestFactory()
    headers = {"REMOTE_ADDR": remote}
    if forwarded is not None:
        headers["HTTP_X_FORWARDED_FOR"] = forwarded
    return factory.get("/", **headers)


def _clear_cooldown(phone: str, purpose: str = OTPChallenge.Purpose.CUSTOMER) -> None:
    """Move the last-request stamp back so only the hourly cap is under test."""
    OTPRequestThrottle.objects.filter(
        scope=OTPRequestThrottle.Scope.PHONE, key=f"{purpose}:{phone}"
    ).update(last_request_at=timezone.now() - timedelta(minutes=5))


# --- cooldown -----------------------------------------------------------------


def test_a_second_request_within_the_cooldown_is_refused():
    request_customer_otp("09131110001")
    with pytest.raises(OTPRateLimitError):
        request_customer_otp("09131110001")


def test_the_cooldown_is_per_phone_not_global():
    request_customer_otp("09131110002")
    request_customer_otp("09131110003")
    assert OTPChallenge.objects.count() == 2


def test_the_cooldown_is_per_purpose():
    """A customer verifying an inquiry must not spend the budget for a staff
    login to the same number."""
    request_customer_otp("09131110004")
    request_login_otp("09131110004")
    assert OTPChallenge.objects.count() == 2


# --- hourly caps --------------------------------------------------------------


def test_the_per_phone_hourly_cap_is_enforced(settings):
    settings.OTP_MAX_REQUESTS_PER_HOUR = 3
    phone = "09131110005"

    for _ in range(3):
        request_customer_otp(phone)
        _clear_cooldown(phone)

    with pytest.raises(OTPRateLimitError):
        request_customer_otp(phone)
    assert OTPChallenge.objects.filter(phone=phone).count() == 3


def test_the_window_rolls_forward_rather_than_accumulating(settings):
    settings.OTP_MAX_REQUESTS_PER_HOUR = 2
    phone = "09131110006"

    request_customer_otp(phone)
    _clear_cooldown(phone)
    request_customer_otp(phone)
    _clear_cooldown(phone)
    with pytest.raises(OTPRateLimitError):
        request_customer_otp(phone)

    OTPRequestThrottle.objects.all().update(
        window_started_at=timezone.now() - timedelta(hours=2),
        last_request_at=timezone.now() - timedelta(hours=2),
    )
    request_customer_otp(phone)
    assert OTPChallenge.objects.filter(phone=phone).count() == 3


def test_the_per_address_cap_covers_a_list_of_numbers(settings):
    """Every other limit keys on the phone, so a caller working through a list of
    numbers would never touch one."""
    settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR = 3
    settings.SANGA_TRUSTED_PROXY_COUNT = 0
    request = _request(remote="198.51.100.7")

    for index in range(3):
        request_customer_otp(f"091311120{index:02d}", request=request)

    with pytest.raises(OTPRateLimitError):
        request_customer_otp("09131112099", request=request)


def test_the_address_cap_spans_both_purposes(settings):
    """Otherwise the budget simply doubles for anyone who alternates."""
    settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR = 2
    request = _request(remote="198.51.100.8")

    request_customer_otp("09131113001", request=request)
    request_login_otp("09131113002", request=request)

    with pytest.raises(OTPRateLimitError):
        request_customer_otp("09131113003", request=request)


# --- the limit is claimed, not merely checked ---------------------------------


def test_a_refused_request_never_writes_a_challenge():
    request_customer_otp("09131114001")
    with pytest.raises(OTPRateLimitError):
        request_customer_otp("09131114001")

    assert OTPChallenge.objects.filter(phone="09131114001").count() == 1


def test_limits_apply_to_unprovisioned_numbers_too():
    """Otherwise the rate limiter answers a question it must not: whether a
    number belongs to a SANGA account."""
    request_login_otp("09131115001")
    with pytest.raises(OTPRateLimitError):
        request_login_otp("09131115001")


# --- which address is counted -------------------------------------------------


def test_a_forwarded_header_is_ignored_when_no_proxy_is_declared(settings):
    settings.SANGA_TRUSTED_PROXY_COUNT = 0
    request = _request(remote="203.0.113.9", forwarded="1.2.3.4")
    assert _client_ip(request) == "203.0.113.9"


def test_one_declared_proxy_reads_the_hop_it_appended(settings):
    settings.SANGA_TRUSTED_PROXY_COUNT = 1
    request = _request(remote="10.0.0.1", forwarded="203.0.113.9")
    assert _client_ip(request) == "203.0.113.9"


def test_a_client_cannot_prepend_its_way_to_a_fresh_key(settings):
    """The attack the old code allowed: invent a leftmost value per request and
    every request looks like a different caller."""
    settings.SANGA_TRUSTED_PROXY_COUNT = 1
    spoofed = _request(remote="10.0.0.1", forwarded="9.9.9.9, 203.0.113.9")
    honest = _request(remote="10.0.0.1", forwarded="203.0.113.9")

    assert _client_ip(spoofed) == _client_ip(honest) == "203.0.113.9"


def test_two_declared_proxies_step_back_two_hops(settings):
    settings.SANGA_TRUSTED_PROXY_COUNT = 2
    request = _request(remote="10.0.0.1", forwarded="203.0.113.9, 10.0.0.2")
    assert _client_ip(request) == "203.0.113.9"


def test_a_missing_header_falls_back_to_the_socket_address(settings):
    settings.SANGA_TRUSTED_PROXY_COUNT = 1
    assert _client_ip(_request(remote="203.0.113.9")) == "203.0.113.9"


def test_spoofing_cannot_throttle_somebody_else(settings):
    """The mirror of the same bug: attributing your traffic to a victim's address
    used to consume their budget."""
    settings.SANGA_TRUSTED_PROXY_COUNT = 0
    settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR = 2
    attacker = _request(remote="198.51.100.66", forwarded="203.0.113.77")

    request_customer_otp("09131116001", request=attacker)
    request_customer_otp("09131116002", request=attacker)
    with pytest.raises(OTPRateLimitError):
        request_customer_otp("09131116003", request=attacker)

    # The address they tried to blame is untouched.
    victim = _request(remote="203.0.113.77")
    request_customer_otp("09131116004", request=victim)


def test_no_request_object_means_no_address_limit():
    """Service-layer callers with no HTTP request still get the phone limits."""
    assert _client_ip(None) is None
    request_customer_otp("09131117001")
    assert not OTPRequestThrottle.objects.filter(
        scope=OTPRequestThrottle.Scope.ADDRESS
    ).exists()


# --- the counter is one row, not a growing table ------------------------------


def test_the_throttle_keeps_one_row_per_key(settings):
    settings.OTP_MAX_REQUESTS_PER_HOUR = 10
    phone = "09131118001"
    request = _request(remote="198.51.100.10")

    for _ in range(3):
        request_customer_otp(phone, request=request)
        _clear_cooldown(phone)

    assert OTPRequestThrottle.objects.filter(scope=OTPRequestThrottle.Scope.PHONE).count() == 1
    assert OTPRequestThrottle.objects.filter(scope=OTPRequestThrottle.Scope.ADDRESS).count() == 1
    assert OTPRequestThrottle.objects.get(scope=OTPRequestThrottle.Scope.PHONE).count == 3
