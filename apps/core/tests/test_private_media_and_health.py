from unittest.mock import patch

import pytest
from django.test import override_settings


@pytest.mark.django_db
@override_settings(DEBUG=True)
@pytest.mark.parametrize("prefix", ["invoice-assets", "invoice-signatures"])
def test_raw_private_media_is_unreachable_even_when_file_exists(client, settings, tmp_path, prefix):
    settings.MEDIA_ROOT = tmp_path
    path = tmp_path / prefix / "users" / "signature.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"private signature")
    # Rebuild URL patterns under DEBUG, as a development server does.
    import importlib

    from django.urls import clear_url_caches

    import config.urls

    original_patterns = config.urls.urlpatterns
    try:
        importlib.reload(config.urls)
        clear_url_caches()
        assert client.get(f"/media/{prefix}/users/signature.png").status_code == 404
    finally:
        config.urls.urlpatterns = original_patterns
        clear_url_caches()


@pytest.mark.django_db
@override_settings(
    DEBUG=False, SECURE_SSL_REDIRECT=True,
    SECURE_REDIRECT_EXEMPT=[r"^health/$"], ALLOWED_HOSTS=["sangesfahan.com"],
)
def test_internal_health_probe_checks_database_while_other_paths_redirect(client):
    assert client.get("/health/", HTTP_HOST="sangesfahan.com").status_code == 200
    assert client.get("/auth/login/", HTTP_HOST="sangesfahan.com").status_code == 301
    with patch("apps.core.views.connection.ensure_connection", side_effect=OSError("database down")):
        assert client.get("/health/", HTTP_HOST="sangesfahan.com").status_code == 503
