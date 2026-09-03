"""Configuration invariants production refuses to start without.

Written as plain functions taking explicit arguments rather than as code reading
``settings`` at import time, so each rule can be tested for every combination
that matters without re-importing a settings module thirty times.

The principle throughout: a misconfiguration that silently destroys or hides data
is worse than one that stops the deployment. A container that will not start gets
noticed in minutes; uploads written to a filesystem nobody mounted get noticed
when a customer asks why the product photos are gone.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured


def check_media_storage(
    *,
    use_s3: bool,
    allow_local_media: bool,
    media_root: str,
    bucket: str = "",
    access_key: str = "",
    secret_key: str = "",
    use_iam_role: bool = False,
    region: str | None = None,
    endpoint: str | None = None,
    csp_img_src: list[str] | None = None,
    csp_media_src: list[str] | None = None,
) -> None:
    """Refuse to serve if uploaded media would be lost or unreachable.

    SANGA production stores media in object storage. The alternative was not a
    second supported strategy, it was an accident: with ``USE_S3=false`` the
    default storage writes to a path inside the container, Django only routes
    ``MEDIA_URL`` while ``DEBUG`` is on, and the production compose file mounts no
    volume there. So every product photo was written to the container's writable
    layer, was never reachable over HTTP, and disappeared on the next deploy —
    with nothing in any log to say so.

    Local media is still allowed, but only when asked for by name, because saying
    so is the step that makes an operator think about the volume and the proxy
    that have to exist for it to work.
    """
    if not use_s3:
        if not allow_local_media:
            raise ImproperlyConfigured(
                "USE_S3=false in production would write uploaded media to the container "
                "filesystem, which Django does not serve when DEBUG=False and which is "
                "discarded when the container is replaced. Configure object storage "
                "(USE_S3=true), or set SANGA_ALLOW_LOCAL_MEDIA=true if this deployment "
                "really does mount a persistent volume at MEDIA_ROOT and serve /media/ "
                "from a reverse proxy."
            )
        if not media_root:
            raise ImproperlyConfigured(
                "SANGA_ALLOW_LOCAL_MEDIA=true requires SANGA_MEDIA_ROOT to name the path "
                "the persistent volume is mounted at, so the mount and the setting cannot "
                "drift apart."
            )
        return

    if not bucket:
        raise ImproperlyConfigured("USE_S3=true requires AWS_STORAGE_BUCKET_NAME.")

    if not use_iam_role and not (access_key and secret_key):
        raise ImproperlyConfigured(
            "USE_S3=true requires AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, or "
            "AWS_S3_USE_IAM_ROLE=true when the host supplies credentials itself."
        )

    if not (region or endpoint):
        raise ImproperlyConfigured(
            "USE_S3=true requires AWS_S3_REGION_NAME or AWS_S3_ENDPOINT_URL, so boto3 "
            "knows which endpoint to sign for. Iranian object stores generally need the "
            "endpoint."
        )

    # Media served from another origin is blocked by the Content-Security-Policy
    # unless that origin is listed. Left unset, every product image and video
    # fails to load and the symptom looks like missing data rather than a header.
    if not (csp_img_src or []):
        raise ImproperlyConfigured(
            "USE_S3=true requires CSP_IMG_SRC to include the object-storage origin, or "
            "the Content-Security-Policy blocks every product image."
        )
    if not (csp_media_src or []):
        raise ImproperlyConfigured(
            "USE_S3=true requires CSP_MEDIA_SRC to include the object-storage origin, or "
            "the Content-Security-Policy blocks every product video."
        )
