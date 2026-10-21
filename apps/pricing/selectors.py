"""Read helpers for pricing.

The per-contact override selectors that used to live here went away with
``ContactPrice`` in V2. Price questions now have exactly two answers — the B2B
tier and the B2C tier — so there is nothing viewer-dependent left to look up.
"""

from __future__ import annotations


