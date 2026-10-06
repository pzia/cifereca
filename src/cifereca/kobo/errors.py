from __future__ import annotations

from typing import Any


class KoboAdeptError(RuntimeError):
    """Legacy normalized error used by the Kobo acquisition coordinator.

    The executable adapter itself lives in the external ``runtime.kobo-adept``
    plugin.  Keeping only the error shape avoids leaking that runtime back into
    the core while preserving ledger/error reconciliation semantics.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        exit_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.exit_code = exit_code
        self.details = dict(details or {})
