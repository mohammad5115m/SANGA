"""Production must not start on a media configuration that loses files.

The failure this guards against left no trace: with ``USE_S3=false`` the default
storage wrote product photos into the container's writable layer, Django never
routed ``MEDIA_URL`` because ``DEBUG`` was off, and the production compose file
mounted nothing at that path. Uploads appeared to succeed, were never reachable
over HTTP, and vanished on the next deploy.

Each rule is exercised through the pure checker rather than by re-importing the
settings module, so every combination can be stated in one line and the reason it
is refused stays visible in the test name.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.checks import check_media_storage

S3 = {
    "use_s3": True,
    "allow_local_media": False,
    "media_root": "",
    "bucket": "sanga-media",
    "access_key": "key",
    "secret_key": "secret",
    "endpoint": "https://storage.example.ir",
    "csp_img_src": ["https://storage.example.ir"],
    "csp_media_src": ["https://storage.example.ir"],
}

LOCAL = {
    "use_s3": False,
    "allow_local_media": True,
    "media_root": "/app/media",
}


def _check(**overrides) -> None:
    base = S3 if overrides.pop("_base", "s3") == "s3" else LOCAL
    check_media_storage(**{**base, **overrides})


# --- the default that used to lose files --------------------------------------


def test_object_storage_off_without_the_explicit_override_is_refused():
    with pytest.raises(ImproperlyConfigured, match="USE_S3"):
        check_media_storage(use_s3=False, allow_local_media=False, media_root="")


def test_naming_a_media_root_is_not_enough_on_its_own():
    """Setting the path without asking for the mode is the shape of a mistake,
    not a decision."""
    with pytest.raises(ImproperlyConfigured):
        check_media_storage(use_s3=False, allow_local_media=False, media_root="/app/media")


# --- the deliberate local-media deployment ------------------------------------


def test_local_media_is_allowed_when_asked_for_by_name():
    _check(_base="local")


def test_local_media_without_a_named_root_is_refused():
    """The point of requiring the path is that writing it down is the step that
    makes somebody check the volume is actually mounted there."""
    with pytest.raises(ImproperlyConfigured, match="SANGA_MEDIA_ROOT"):
        _check(_base="local", media_root="")


# --- object storage -----------------------------------------------------------


def test_a_complete_object_storage_configuration_is_accepted():
    _check()


def test_object_storage_without_a_bucket_is_refused():
    with pytest.raises(ImproperlyConfigured, match="AWS_STORAGE_BUCKET_NAME"):
        _check(bucket="")


@pytest.mark.parametrize("missing", ["access_key", "secret_key"])
def test_object_storage_with_half_a_credential_pair_is_refused(missing):
    with pytest.raises(ImproperlyConfigured, match="AWS_ACCESS_KEY"):
        _check(**{missing: ""})


def test_object_storage_may_take_its_credentials_from_the_host():
    """An instance profile is better than long-lived keys in the environment,
    so it must not be refused for having no keys."""
    _check(access_key="", secret_key="", use_iam_role=True)


def test_object_storage_without_a_region_or_endpoint_is_refused():
    with pytest.raises(ImproperlyConfigured, match="AWS_S3_REGION_NAME"):
        _check(endpoint=None, region=None)


def test_a_region_alone_is_enough():
    _check(endpoint=None, region="eu-central-1")


# --- the header that would otherwise hide every image -------------------------


def test_object_storage_without_an_image_source_in_the_policy_is_refused():
    """Left unset, the Content-Security-Policy blocks every product image and the
    symptom looks like missing data rather than a missing header."""
    with pytest.raises(ImproperlyConfigured, match="CSP_IMG_SRC"):
        _check(csp_img_src=[])


def test_object_storage_without_a_media_source_in_the_policy_is_refused():
    with pytest.raises(ImproperlyConfigured, match="CSP_MEDIA_SRC"):
        _check(csp_media_src=[])


def test_local_media_does_not_need_a_policy_origin():
    """It is served from the site's own origin, which 'self' already covers."""
    _check(_base="local", csp_img_src=[], csp_media_src=[])
