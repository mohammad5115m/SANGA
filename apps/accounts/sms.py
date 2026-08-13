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


#: Every provider SANGA knows how to build. A name that is not in here is a
#: configuration error, not a reason to guess.
PROVIDERS: dict[str, type[SmsProvider]] = {
    "console": ConsoleSmsProvider,
    "null": NullSmsProvider,
    # Real gateways (kavenegar, ghasedak, …) register here. Each must set
    # ``delivers = True`` and be usable in production.
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
