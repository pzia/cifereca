"""#4496: strict reader for the Core-owned destructive maintenance preference.

The reader is not an execution grant. Core must enforce it at the live
commit boundary independently of plan options, confirmation and proofs.
"""
from __future__ import annotations

from ..config import Config

DESTRUCTIVE_CLEANUP_KEY = "maintenance.destructive_cleanup_enabled"


def destructive_cleanup_enabled(config: Config | None) -> bool:
    """Only a Core Config object's literal boolean true enables the policy."""
    if type(config) is not Config:
        return False
    try:
        return config.get(DESTRUCTIVE_CLEANUP_KEY, False) is True
    except (TypeError, ValueError, AttributeError, RuntimeError):
        return False


def cleanup_review_epoch(config: Config | None) -> str | None:
    """Opaque, process-local freshness witness, never a mutation grant."""
    if type(config) is not Config:
        return None
    value = getattr(config, "_cleanup_review_epoch", None)
    return value if (type(value) is str and len(value) == 32
                     and all(c in "0123456789abcdef" for c in value)) else None
