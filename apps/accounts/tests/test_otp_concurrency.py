"""One code, two requests, same instant.

Runs only on the PostgreSQL lane. The verification path was read-check-write: two
requests carrying the same correct code both read ``is_used=False``, both
concluded they had won, and a code that is one-time by definition was used twice.
Wrong guesses had the mirror problem — concurrent attempts read the same counter
and wrote back the same increment, so the attempt limit could be walked past by
guessing in parallel.

SQLite cannot demonstrate either: it serializes writers behind one database lock,
so the second request always arrives after the first has finished.
"""

from __future__ import annotations

import itertools
import threading

import pytest
from django.db import connection
from django.test import RequestFactory

from apps.accounts.models import OTPChallenge
from apps.accounts.services import (
    OTPRateLimitError,
    OTPValidationError,
    _hash_code,
    request_customer_otp,
    verify_customer_otp,
)

pytestmark = [pytest.mark.concurrency, pytest.mark.django_db(transaction=True)]


def _race(target, count: int = 2) -> tuple[list, list]:
    barrier = threading.Barrier(count, timeout=15)
    results: list = []
    errors: list = []
    lock = threading.Lock()

    def runner() -> None:
        try:
            barrier.wait()
            outcome = target()
        except Exception as exc:  # noqa: BLE001 - the test inspects what escaped
            with lock:
                errors.append(exc)
        else:
            with lock:
                results.append(outcome)
        finally:
            connection.close()

    threads = [threading.Thread(target=runner) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


def _code(phone: str) -> str:
    challenge = OTPChallenge.objects.filter(phone=phone).order_by("-created_at").first()
    for candidate in range(1000000):
        code = f"{candidate:06d}"
        if _hash_code(code) == challenge.code_hash:
            return code
    raise AssertionError("code not found")


def test_only_one_of_two_simultaneous_verifications_succeeds(settings):
    settings.SMS_PROVIDER = "console"
    phone = "09129997001"
    request_customer_otp(phone)
    code = _code(phone)

    results, errors = _race(lambda: verify_customer_otp(phone, code))

    assert len(results) == 1, f"a one-time code was accepted twice; errors={errors}"
    assert all(isinstance(error, OTPValidationError) for error in errors), errors
    assert OTPChallenge.objects.get(phone=phone).is_used is True


def test_parallel_wrong_guesses_each_cost_an_attempt(settings):
    """Lost updates here would let the attempt limit be walked past by guessing
    in parallel instead of in sequence."""
    settings.SMS_PROVIDER = "console"
    settings.OTP_MAX_ATTEMPTS = 10
    phone = "09129997002"
    request_customer_otp(phone)

    _results, errors = _race(lambda: verify_customer_otp(phone, "000000"), count=5)

    assert len(errors) == 5
    assert OTPChallenge.objects.get(phone=phone).attempts == 5


def test_a_burned_code_stays_burned_under_contention(settings):
    settings.SMS_PROVIDER = "console"
    phone = "09129997003"
    request_customer_otp(phone)
    code = _code(phone)

    assert verify_customer_otp(phone, code) is True

    results, errors = _race(lambda: verify_customer_otp(phone, code), count=3)
    assert results == []
    assert len(errors) == 3


# --- the request side -----------------------------------------------------------
#
# Verification was serialized; requesting a code was not. The limits were three
# COUNT queries followed, in a *different* transaction, by the INSERT they were
# meant to be limiting — so simultaneous requests all counted the same rows, all
# concluded they were under the limit, and all inserted. Every limit here could be
# walked past by sending requests in parallel instead of in sequence.


def test_simultaneous_requests_for_one_phone_yield_one_code(settings):
    """The cooldown exists so a phone receives one message per minute. Two
    requests at the same instant used to send two."""
    settings.SMS_PROVIDER = "console"
    phone = "09129998001"

    results, errors = _race(lambda: request_customer_otp(phone))

    assert len(results) == 1, f"the cooldown was bypassed; errors={errors}"
    assert all(isinstance(error, OTPRateLimitError) for error in errors), errors
    assert OTPChallenge.objects.filter(phone=phone).count() == 1


def test_the_per_phone_hourly_cap_holds_under_parallel_requests(settings):
    settings.SMS_PROVIDER = "console"
    settings.OTP_MAX_REQUESTS_PER_HOUR = 3
    settings.OTP_REQUEST_COOLDOWN_SECONDS = 0
    phone = "09129998002"

    _results, _errors = _race(lambda: request_customer_otp(phone), count=8)

    assert OTPChallenge.objects.filter(phone=phone).count() == 3


def test_the_per_address_cap_holds_under_parallel_requests(settings):
    """Different phone each time, one address: the limit that stops somebody
    working through a list of numbers."""
    settings.SMS_PROVIDER = "console"
    settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR = 3
    settings.SANGA_TRUSTED_PROXY_COUNT = 0
    counter = itertools.count()
    lock = threading.Lock()

    def ask():
        with lock:
            index = next(counter)
        request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.200")
        return request_customer_otp(f"091299983{index:02d}", request=request)

    _results, _errors = _race(ask, count=8)

    assert OTPChallenge.objects.count() == 3


def test_two_phones_behind_one_address_do_not_deadlock(settings):
    """Both locks are always taken phone-first. Taking them in different orders
    is how two requests wait on each other forever."""
    settings.SMS_PROVIDER = "console"
    settings.OTP_MAX_REQUESTS_PER_IP_PER_HOUR = 100
    settings.SANGA_TRUSTED_PROXY_COUNT = 0
    phones = itertools.cycle(["09129998401", "09129998402"])
    lock = threading.Lock()

    def ask():
        with lock:
            phone = next(phones)
        request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.201")
        return request_customer_otp(phone, request=request)

    results, errors = _race(ask, count=6)

    assert len(results) + len(errors) == 6, "a thread never finished; suspect a deadlock"
    assert all(isinstance(error, OTPRateLimitError) for error in errors), errors
