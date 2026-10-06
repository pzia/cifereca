from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..models import Account, Booking, HistoryItem, ItemDetail, Loan, SearchResult


ImplementationState = Literal["ready", "skeleton"]


class ProviderError(RuntimeError):
    pass


class ProviderConfigurationError(ProviderError):
    pass


class ProviderCapabilityError(ProviderError):
    pass


@dataclass(frozen=True)
class ProviderCapabilities:
    authentication: bool = False
    multiple_credentials: bool = False
    linked_accounts: bool = False
    search: bool = False
    loans: bool = False
    history: bool = False
    bookings: bool = False
    holdings: bool = False
    borrow: bool = False
    digital_download: bool = False
    direct_download: bool = False
    personal_library: bool = False
    backup: bool = False
    return_loan: bool = False
    renew_loan: bool = False

    def names(self) -> list[str]:
        return [name for name, value in self.__dict__.items() if value]


@dataclass(frozen=True)
class CredentialField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    display_name: str
    description: str
    state: ImplementationState
    capabilities: ProviderCapabilities
    credential_fields: tuple[CredentialField, ...] = ()
    homepage: str | None = None
    notes: tuple[str, ...] = ()


class ProviderSession(ABC):
    """One authenticated/configured provider identity.

    A plugin may expose several logical reader accounts through one provider
    session (Paris linked accounts are the first example). `credential_id` is
    stable local configuration identity; `Account.unique_id` is the upstream
    reader identity inside that session.
    """

    def __init__(
        self,
        *,
        descriptor: ProviderDescriptor,
        credential_id: str,
        label: str,
        credentials: dict[str, Any],
        options: dict[str, Any],
    ) -> None:
        self.descriptor = descriptor
        self.provider_id = descriptor.id
        self.credential_id = credential_id
        self.label = label or descriptor.display_name
        self.credentials = dict(credentials)
        self.options = dict(options)
        self._accounts: list[Account] = []
        self.connected = False

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self.descriptor.capabilities

    @property
    def accounts(self) -> list[Account]:
        return list(self._accounts)

    @abstractmethod
    def connect(self) -> list[Account]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    def loans(self, account: Account) -> list[Loan]:
        raise ProviderCapabilityError(f"{self.provider_id}: loans unsupported")

    def history(self, account: Account) -> list[HistoryItem]:
        raise ProviderCapabilityError(f"{self.provider_id}: history unsupported")

    def bookings(self, account: Account) -> list[Booking]:
        raise ProviderCapabilityError(f"{self.provider_id}: bookings unsupported")

    def search(self, query: str, scope: str = "all", page: int = 0, field: str = "all") -> list[SearchResult]:
        raise ProviderCapabilityError(f"{self.provider_id}: search unsupported")

    def holdings(self, item: SearchResult, search_terms: str = "") -> ItemDetail:
        raise ProviderCapabilityError(f"{self.provider_id}: holdings unsupported")

    def library(self) -> list[SearchResult]:
        raise ProviderCapabilityError(f"{self.provider_id}: personal library unsupported")

    def backup_manifest(self) -> list[dict[str, Any]]:
        raise ProviderCapabilityError(f"{self.provider_id}: backup manifest unsupported")

    def download_library_item(self, item: dict[str, Any], target_dir: Path) -> tuple[Path, str, dict[str, Any]]:
        raise ProviderCapabilityError(f"{self.provider_id}: library backup unsupported")

    def borrow(self, item: SearchResult, account: Account) -> Loan:
        """Create a loan and return the normalized resulting loan.

        The Paris plugin intentionally leaves this unsupported until the exact
        Syracuse/PNB mutation has been captured and validated.
        """
        raise ProviderCapabilityError(f"{self.provider_id}: borrow unsupported")

    def download_direct(self, item: SearchResult, target_dir: Path) -> tuple[Path, str]:
        """Acquire a non-loan digital resource (e.g. future Gallica plugin)."""
        raise ProviderCapabilityError(f"{self.provider_id}: direct download unsupported")

    def renew(self, loan: Loan, account: Account) -> dict[str, Any]:
        raise ProviderCapabilityError(f"{self.provider_id}: renew unsupported")

    def return_digital(self, loan: Loan, account: Account) -> dict[str, Any]:
        raise ProviderCapabilityError(f"{self.provider_id}: return unsupported")

    def digital_preferences(self, account: Account) -> dict[str, Any]:
        return {}

    def download_digital(self, loan: Loan, account: Account, target_dir: Path) -> tuple[Path, str]:
        raise ProviderCapabilityError(f"{self.provider_id}: digital download unsupported")

    def diagnostic_state(self) -> dict[str, Any]:
        # Never include credentials or cookies here.
        return {
            "provider": self.provider_id,
            "credential_id": self.credential_id,
            "connected": self.connected,
            "accounts": len(self._accounts),
        }


class ProviderPlugin(ABC):
    descriptor: ProviderDescriptor

    @abstractmethod
    def create_session(
        self,
        *,
        credential_id: str,
        label: str,
        credentials: dict[str, Any],
        options: dict[str, Any],
        config: Any,
        storage: Any,
        diagnostics: Any,
    ) -> ProviderSession:
        raise NotImplementedError
