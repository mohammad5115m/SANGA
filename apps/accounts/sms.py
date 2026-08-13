from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger("apps.accounts.sms")


@dataclass(frozen=True)
class SmsMessage:
    phone: str
    body: str


class SmsProvider(ABC):
    #: Whether this provider actually delivers a message to a handset. Providers
    #: that do not are refused in production unless explicitly allowed, because a
    #: deployment whose OTPs go nowhere is not a working deployment — it is one
    #: where nobody can log in and the codes are sitting in a log file.
    delivers = False

    @abstractmethod
    def send(self, message: SmsMessage) -> None:
        raise NotImplementedError


class ConsoleSmsProvider(SmsProvider):
    """Development-safe provider: logs the OTP instead of sending it."""

    delivers = False

    def send(self, message: SmsMessage) -> None:
        logger.info("SMS to %s :: %s", message.phone, message.body)


class NullSmsProvider(SmsProvider):
    """Sends nothing and says nothing. For tests that must not touch a gateway."""

    delivers = False

    def send(self, message: SmsMessage) -> None:
        return None


class SmsDeliveryError(Exception):
    """The gateway did not accept the message.

    Deliberately carries no vendor payload. Callers turn this into one Persian
    sentence for the user, and the detail belongs in the log — where the API key
    and the code must not follow it.
    """


class KavenegarSmsProvider(SmsProvider):
    """Delivery through Kavenegar's transactional endpoint.

    Uses ``verify/lookup`` rather than the general send API, because that is the
    endpoint Iranian operators permit outside daytime hours and do not filter as
    bulk traffic — an OTP that arrives at 2am only if the recipient is lucky is
    not an OTP. It takes a pre-approved template and substitutes one token, so
    this provider extracts the code from the message body rather than sending the
    body itself.

    Built on ``urllib`` rather than ``requests``. One POST to one endpoint does
    not justify adding an HTTP library, and every dependency that touches
    credentials is one more thing to keep patched.

    **The API key travels in the URL path.** Nothing here ever logs a URL, and
    the one log line that exists on the failure path carries a status code and
    nothing else. The message body is not logged either: it contains the code.
    """

    delivers = True

    #: ``{key}`` is the API key, so this string must never be formatted into a
    #: log line, an exception message or an error report.
    ENDPOINT = "https://api.kavenegar.com/v1/{key}/verify/lookup.json"

    def __init__(self) -> None:
        self.api_key = (getattr(settings, "KAVENEGAR_API_KEY", "") or "").strip()
        self.template = (getattr(settings, "KAVENEGAR_OTP_TEMPLATE", "") or "").strip()
        self.timeout = float(getattr(settings, "SMS_TIMEOUT_SECONDS", 10) or 10)
        self.validate_configuration()

    @classmethod
    def validate_configuration(cls) -> None:
        """Refuse at construction rather than at the first login attempt.

        Called from production settings at import, so a missing key stops the
        deployment instead of turning into an outage the first time somebody
        tries to sign in.
        """
        missing = [
            name
            for name, value in (
                ("KAVENEGAR_API_KEY", getattr(settings, "KAVENEGAR_API_KEY", "")),
                ("KAVENEGAR_OTP_TEMPLATE", getattr(settings, "KAVENEGAR_OTP_TEMPLATE", "")),
            )
            if not (value or "").strip()
        ]
        if missing:
            raise ImproperlyConfigured(
                f"SMS_PROVIDER=kavenegar requires {', '.join(missing)}. "
                "The template must be one already approved in the Kavenegar panel."
            )

    @staticmethod
    def _token(body: str) -> str:
        """The one substitution the approved template takes: the code itself.

        The body is built by the OTP service as «کد ورود سنگا: 123456»; the
        lookup endpoint wants only the digits.
        """
        digits = "".join(part for part in body if part.isdigit())
        if not digits:
            raise SmsDeliveryError("no verification token in the message body")
        return digits

    def send(self, message: SmsMessage) -> None:
        import json
        import urllib.error
        import urllib.parse
        import urllib.request

        payload = urllib.parse.urlencode(
            {
                "receptor": message.phone,
                "token": self._token(message.body),
                "template": self.template,
            }
        ).encode()

        request = urllib.request.Request(
            self.ENDPOINT.format(key=self.api_key),
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            # exc carries the request URL, and the URL carries the API key.
            logger.warning("SMS gateway refused a message with status %s", exc.code)
            raise SmsDeliveryError(f"gateway returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("SMS gateway unreachable: %s", type(exc).__name__)
            raise SmsDeliveryError("gateway unreachable") from None

        # A 200 is not an acceptance. Kavenegar reports refusals — an unapproved
        # template, an exhausted balance, a blocked number — in the body, and
        # treating the transport status as the outcome means those all look like
        # a delivered code that never arrived.
        try:
            status = json.loads(body).get("return", {}).get("status")
        except (ValueError, AttributeError) as exc:
            raise SmsDeliveryError("unreadable gateway response") from exc

        if status != 200:
            logger.warning("SMS gateway reported status %s", status)
            raise SmsDeliveryError(f"gateway status {status}")


#: Every provider SANGA knows how to build. A name that is not in here is a
#: configuration error, not a reason to guess.
PROVIDERS: dict[str, type[SmsProvider]] = {
    "console": ConsoleSmsProvider,
    "null": NullSmsProvider,
    "kavenegar": KavenegarSmsProvider,
}


def get_sms_provider() -> SmsProvider:
    """Build the configured provider, or refuse.

    An unknown name used to fall back to the console provider with a warning.
    That is the worst possible behaviour for this setting: a typo in
    ``SMS_PROVIDER`` produced a deployment that looked healthy, sent nothing to
    anyone, and wrote every login code into the application log — where it stays
    long after the code expires. Nobody notices until real users cannot log in,
    and by then the logs are an authentication bypass.
    """
    name = (getattr(settings, "SMS_PROVIDER", "") or "").strip().lower()
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ImproperlyConfigured(
            f"SMS_PROVIDER={name!r} is not a provider SANGA knows. "
            f"Choose one of: {', '.join(sorted(PROVIDERS))}."
        )
    return provider()
