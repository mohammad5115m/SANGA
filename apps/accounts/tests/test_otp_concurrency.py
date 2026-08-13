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

import threading

import pytest
from django.db import connection

from apps.accounts.models import OTPChallenge
from apps.accounts.services import (
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
