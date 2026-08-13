"""Form fields shared across apps."""

from __future__ import annotations

from django import forms


class HttpsURLField(forms.URLField):
    """A URL field that reads a scheme-less entry as ``https``.

    Django 6.0 makes this the default. Until then a plain ``URLField`` warns on
    every instantiation unless something says which scheme to assume, and the
    two ways to say it are not equal: ``FORMS_URLFIELD_ASSUME_HTTPS`` is a
    transitional setting that is itself deprecated and removed in 6.0, so opting
    in through it trades one warning for another. Declaring it on the field is
    the version that survives the upgrade.

    A business typing ``sanga.ir`` into their profile means the https site.
    Defaulting to http would store a link that browsers now warn about.
    """

    def __init__(self, *args, **kwargs):
        kwargs["assume_scheme"] = kwargs.get("assume_scheme") or "https"
        super().__init__(*args, **kwargs)
