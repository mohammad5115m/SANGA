"""Static UI contracts for accessibility, CSP safety, and audience boundaries."""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

TEMPLATE_ROOT = Path(settings.BASE_DIR) / "templates"


def _templates() -> list[Path]:
    return sorted(TEMPLATE_ROOT.rglob("*.html"))


def test_templates_do_not_use_inline_event_handlers():
    inline_event = re.compile(r"\son(?:click|change|submit|input|load)\s*=", re.IGNORECASE)
    offenders = [str(path.relative_to(TEMPLATE_ROOT)) for path in _templates() if inline_event.search(path.read_text())]

    assert offenders == [], f"CSP-blocked inline handlers: {offenders}"


def test_templates_do_not_use_multiline_short_comments():
    """Django's ``{# ... #}`` comments end at the first newline.

    A multiline short comment is emitted as visible page text, which can expose
    implementation notes on both authenticated and public surfaces. Use the
    ``{% comment %}`` block tag for comments that span lines.
    """
    multiline_short_comment = re.compile(r"\{#(?:(?!#\})[^\n])*\n")
    offenders = [
        str(path.relative_to(TEMPLATE_ROOT))
        for path in _templates()
        if multiline_short_comment.search(path.read_text())
    ]

    assert offenders == [], f"multiline Django short comments: {offenders}"


def test_every_table_header_has_an_explicit_scope():
    unscoped_header = re.compile(r"<th(?!ead\b)(?![^>]*\bscope=)[^>]*>", re.IGNORECASE)
    offenders = [
        str(path.relative_to(TEMPLATE_ROOT))
        for path in _templates()
        if unscoped_header.search(path.read_text())
    ]

    assert offenders == [], f"table headers without scope: {offenders}"


def test_print_controls_are_csp_safe():
    templates_and_controls = {
        "invoicing/document.html": "data-print-invoice",
        "accounting/statement_print.html": "data-print-action",
        "reporting/print.html": "data-print-action",
    }
    for name, control in templates_and_controls.items():
        body = (TEMPLATE_ROOT / name).read_text()
        assert control in body
        assert "onclick" not in body


def test_public_surfaces_do_not_render_internal_millimetre_values():
    for name in (
        "marketplace/lot_detail.html",
        "catalog/compare.html",
        "trading/request_form.html",
    ):
        body = (TEMPLATE_ROOT / name).read_text()
        assert "thickness_mm" not in body
        assert "thickness_cm" in body


def test_inquiry_review_has_no_nested_form():
    body = (TEMPLATE_ROOT / "catalog/inquiry_review.html").read_text()

    assert body.count("<form") == 1
    assert "formaction" in body


def test_global_accessibility_and_interaction_hooks_exist():
    base = (TEMPLATE_ROOT / "base.html").read_text()
    script = (Path(settings.BASE_DIR) / "static/js/app.js").read_text()

    assert 'class="skip-link"' in base
    assert 'id="async-status"' in base
    assert 'aria-current="page"' in (TEMPLATE_ROOT / "layouts/app_shell.html").read_text()
    assert 'event.key === "ArrowDown"' in script
    assert 'event.key === "Escape"' in script
    assert 'query.setAttribute("role", "combobox")' in script
    assert 'window.addEventListener("beforeunload"' in script
    assert 'form.dataset.submitting = "true"' in script


def test_product_surface_icons_have_intrinsic_dimensions():
    """Inline SVGs stay compact even while an installed PWA refreshes its CSS."""
    names = (
        "layouts/app_shell.html",
        "inventory/_filter_bar.html",
        "inventory/lot_list.html",
        "marketplace/home.html",
        "catalog/_share_actions.html",
    )
    opening_svg = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)
    missing_dimensions: list[str] = []
    for name in names:
        body = (TEMPLATE_ROOT / name).read_text()
        for tag in opening_svg.findall(body):
            if 'width="' not in tag or 'height="' not in tag:
                missing_dimensions.append(f"{name}: {tag}")

    assert missing_dimensions == []


def test_pwa_refreshes_static_assets_instead_of_serving_stale_css():
    base = (TEMPLATE_ROOT / "base.html").read_text()
    app_script = (Path(settings.BASE_DIR) / "static/js/app.js").read_text()
    worker = (Path(settings.BASE_DIR) / "static/js/sw.js").read_text()

    assert "app.css' %}?v=3" in base
    assert 'const SHELL_CACHE = "sanga-shell-v3"' in worker
    assert 'url.pathname.startsWith("/static/")' in worker
    assert "networkFirst(request)" in worker
    assert '"/static/css/app.css"' not in worker
    assert 'updateViaCache: "none"' in app_script
    assert "registration.update()" in app_script
