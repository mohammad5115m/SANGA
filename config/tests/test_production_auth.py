import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.checks import check_production_auth, check_production_database

SECRET = "test-only-production-configuration-secret-0123456789abcdef"


@pytest.mark.parametrize("secret", [
    "unsafe-dev-only-change-me", "change-me-to-a-long-random-string",
    "x" * 60, "django-insecure-" + "abcdefgh0123456789" * 4,
])
def test_production_refuses_weak_or_development_secrets(secret):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        check_production_auth(secret_key=secret, login_allowlist=[])


def test_production_refuses_demo_provisioning_even_with_a_valid_secret():
    with pytest.raises(ImproperlyConfigured, match="SANGA_LOGIN_PHONE_ALLOWLIST"):
        check_production_auth(secret_key=SECRET, login_allowlist=["09121111111"])


def test_closed_production_auth_configuration_is_accepted():
    check_production_auth(secret_key=SECRET, login_allowlist=[])


def test_production_requires_real_row_lock_support():
    with pytest.raises(ImproperlyConfigured, match="PostgreSQL"):
        check_production_database(engine="django.db.backends.sqlite3")
    check_production_database(engine="django.db.backends.postgresql")
