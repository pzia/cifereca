"""Backward-compatible Core import of the public, closed diagnostics taxonomy.

Only Core's validated producers may attach a domain to a projected event.
"""
from ..plugin_api.diagnostic_domains import (
    DIAGNOSTIC_DOMAINS, canonical_diagnostic_domain,
)
