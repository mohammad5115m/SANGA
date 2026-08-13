"""The production SMS gateway.

Every test here mocks the transport. A test that opens a socket to a vendor is
not a test — it fails when the vendor is slow, passes when a firewall silently
swallows the request, and costs money on a good day.

What is actually being pinned: that a refusal reported inside a 200 response is
treated as a failure, that a timeout does not become a hang, and that neither the
API key nor the verification code ever reaches a log line. The last one matters
most: the key travels in the URL path, so anything that logs a URL on this path
publishes the credential.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.accounts.sms import (
    KavenegarSmsProvider,
    SmsDeliveryError,
    SmsMessage,
    get_sms_provider,
)

API_KEY = "test-key-never-real-0123456789"
CODE = "483920"
MESSAGE = SmsMessage(phone="09121110000", body=f"کد ورود سنگا: {CODE}")


@pytest.fixture(autouse=True)
def configured(settings):
    settings.SMS_PROVIDER = "kavenegar"
    settings.KAVENEGAR_API_KEY = API_KEY
    settings.KAVENEGAR_OTP_TEMPLATE = "sanga-login"
    settings.SMS_TIMEOUT_SECONDS = 3.0


def _response(payload: dict):
    class _Fake(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Fake(json.dumps(payload).encode())


def _accepted():
    return _response({"return": {"status": 200, "message": "ok"}})


# --- the happy path -----------------------------------------------------------


def test_the_provider_is_selectable_by_name():
    assert isinstance(get_sms_provider(), KavenegarSmsProvider)


def test_the_provider_declares_that_it_delivers():
    """Production refuses to start on a provider that does not, so this flag is
    the thing that makes a real deployment possible at all."""
    assert KavenegarSmsProvider.delivers is True


def test_an_accepted_message_returns_quietly():
    with patch("urllib.request.urlopen", return_value=_accepted()) as urlopen:
        get_sms_provider().send(MESSAGE)

    assert urlopen.call_count == 1


def test_the_request_carries_the_recipient_the_token_and_the_template():
    with patch("urllib.request.urlopen", return_value=_accepted()) as urlopen:
        get_sms_provider().send(MESSAGE)

    sent = urlopen.call_args.args[0]
    body = sent.data.decode()
    assert "receptor=09121110000" in body
    assert f"token={CODE}" in body
    assert "template=sanga-login" in body


def test_the_configured_timeout_is_passed_to_the_transport():
    """Without one, a hung gateway hangs the login page."""
    with patch("urllib.request.urlopen", return_value=_accepted()) as urlopen:
        get_sms_provider().send(MESSAGE)

    assert urlopen.call_args.kwargs["timeout"] == 3.0


# --- failures are failures ----------------------------------------------------


def test_a_refusal_inside_a_200_response_is_not_a_delivery():
    """An unapproved template, an exhausted balance or a blocked number all come
    back as HTTP 200 with a status in the body. Trusting the transport status
    would make every one of them look like a code that was sent."""
    refused = _response({"return": {"status": 411, "message": "invalid receptor"}})
    with patch("urllib.request.urlopen", return_value=refused):
        with pytest.raises(SmsDeliveryError):
            get_sms_provider().send(MESSAGE)


def test_an_http_error_becomes_a_controlled_failure():
    error = urllib.error.HTTPError(
        url="https://api.kavenegar.com/v1/…/verify/lookup.json",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(SmsDeliveryError):
            get_sms_provider().send(MESSAGE)


def test_a_timeout_becomes_a_controlled_failure():
    with patch("urllib.request.urlopen", side_effect=TimeoutError):
        with pytest.raises(SmsDeliveryError):
            get_sms_provider().send(MESSAGE)


def test_an_unreachable_gateway_becomes_a_controlled_failure():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        with pytest.raises(SmsDeliveryError):
            get_sms_provider().send(MESSAGE)


def test_an_unreadable_response_becomes_a_controlled_failure():
    with patch("urllib.request.urlopen", return_value=_response({"unexpected": True})):
        with pytest.raises(SmsDeliveryError):
            get_sms_provider().send(MESSAGE)


def test_a_message_with_no_code_in_it_is_refused_before_anything_is_sent():
    with patch("urllib.request.urlopen") as urlopen:
        with pytest.raises(SmsDeliveryError):
            get_sms_provider().send(SmsMessage(phone="09121110000", body="بدون کد"))

    assert urlopen.call_count == 0


# --- nothing sensitive is ever logged -----------------------------------------


@pytest.mark.parametrize(
    "outcome",
    [
        {"return_value": _response({"return": {"status": 411}})},
        {"side_effect": TimeoutError},
        {"side_effect": urllib.error.URLError("no route")},
    ],
)
def test_no_failure_path_logs_the_key_or_the_code(caplog, outcome):
    """The key is in the URL and the code is in the body, so a log line carrying
    either one publishes a credential that outlives the request."""
    with caplog.at_level("DEBUG", logger="apps.accounts.sms"):
        with patch("urllib.request.urlopen", **outcome):
            with pytest.raises(SmsDeliveryError):
                get_sms_provider().send(MESSAGE)

    logged = caplog.text
    assert API_KEY not in logged
    assert CODE not in logged
    assert "kavenegar.com" not in logged


def test_an_http_error_message_never_carries_the_key():
    """``HTTPError`` stringifies to include its URL, so re-raising it as-is would
    have put the key into whatever caught it."""
    error = urllib.error.HTTPError(
        url=f"https://api.kavenegar.com/v1/{API_KEY}/verify/lookup.json",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(SmsDeliveryError) as raised:
            get_sms_provider().send(MESSAGE)

    assert API_KEY not in str(raised.value)
    assert raised.value.__cause__ is None, "the vendor exception must not be chained through"


# --- configuration ------------------------------------------------------------


@pytest.mark.parametrize("missing", ["KAVENEGAR_API_KEY", "KAVENEGAR_OTP_TEMPLATE"])
def test_missing_credentials_are_refused_rather_than_discovered_at_login(settings, missing):
    setattr(settings, missing, "")
    with pytest.raises(ImproperlyConfigured, match=missing):
        get_sms_provider()


def test_an_unknown_provider_name_is_still_refused(settings):
    settings.SMS_PROVIDER = "not-a-gateway"
    with pytest.raises(ImproperlyConfigured):
        get_sms_provider()
