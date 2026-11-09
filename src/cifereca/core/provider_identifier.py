"""Provider identifier grammar shared by redacted Core and scoped CoreUI views.

A provider identifier is a structural routing key, never user-supplied
metadata, a title, credential or proof of provider authorization.
"""
from __future__ import annotations

import re

_PROVIDER_ID = re.compile(r"[a-z][a-z0-9]*(?:[-.][a-z0-9]+)*\Z")
_RESERVED = frozenset({"all", "other", "unattributed"})


def valid_provider_identifier(value: object) -> bool:
    """Accept bounded structural IDs, including providers not yet installed."""
    return (type(value) is str and len(value) <= 64
            and value not in _RESERVED
            and _PROVIDER_ID.fullmatch(value) is not None)
