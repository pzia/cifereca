from __future__ import annotations


class PluginApiError(RuntimeError):
    """Base error exposed by the public plugin API."""


class UserFacingError(PluginApiError):
    """An operation failed with a stable code and a message safe for the UI."""

    def __init__(self, message: str, *, code: str, technical_detail: str | None = None) -> None:
        super().__init__(message)
        self.code = str(code)
        self.user_message = str(message)
        self.technical_detail = str(technical_detail or "")


class AttentionRequired(UserFacingError):
    """A remediable condition that should not be presented as an opaque failure."""


class UnsupportedOperation(PluginApiError):
    pass


class AuthenticationRequired(PluginApiError):
    pass


class ConfigurationRequired(PluginApiError):
    pass


class TemporaryUnavailable(PluginApiError):
    pass


class NotFound(PluginApiError):
    pass


class Conflict(PluginApiError):
    pass


class Cancelled(PluginApiError):
    pass

class BorrowRequestNotSubmitted(PluginApiError):
    """Provider attests it did NOT attempt a mutating request.

    A caller may release its durable borrow claim ONLY on this narrowly
    scoped signal, emitted by the trusted provider *before* entering the
    POST transport. A timeout, status 4xx/5xx or other exception after
    attempting a POST must NEVER produce this signal.
    """



__all__ = [
    "PluginApiError", "UserFacingError", "AttentionRequired", "UnsupportedOperation",
    "AuthenticationRequired", "ConfigurationRequired", "TemporaryUnavailable",
    "NotFound", "Conflict", "Cancelled", "BorrowRequestNotSubmitted",
]
