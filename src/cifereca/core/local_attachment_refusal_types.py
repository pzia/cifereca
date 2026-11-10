"""Exact Core-owned exception classes for local attachment refusal projection.

These exceptions carry a closed reason code, not a supplier right, trusted
receipt or permission to reconcile. Keep class identity stable for legacy
facades and the fail-closed GTK reason projector.
"""
from __future__ import annotations


class ReconciliationRejected(ValueError):
    """A fixed public reason, never a provider exception or private target."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)
