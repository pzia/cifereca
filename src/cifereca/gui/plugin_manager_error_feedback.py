"""Safe GUI boundary for plugin-manager failures (P0 #1143).

Plugin exceptions, parser messages, ZIP paths and system errors are not safe
for display or screenshot capture. Only explicit public UserFacingError
messages may be displayed verbatim; the dialog title supplies operation
context and the technical diagnosis remains in targeted Diagnostics.
"""
from __future__ import annotations

from ..plugin_api import UserFacingError


def plugin_error_message(exc: BaseException) -> str:
    """Do not call __str__/__repr__ on arbitrary plugin or path exceptions."""
    if isinstance(exc, UserFacingError):
        return exc.user_message
    return "Opération sur le plugin indisponible"
