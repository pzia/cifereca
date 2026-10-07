from __future__ import annotations

from ..providers.base import ProviderError


class KoboVaultError(ProviderError):
    """Compatibility error type used by the Core Kobo orchestrator.

    The actual Kobo-Vault HTTP client is provider-owned and lives in
    `plugins/provider-kobo/cifereca_kobo_vault.py`.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


__all__ = ["KoboVaultError"]
