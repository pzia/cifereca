from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..models import Account, Booking, HistoryItem, ItemDetail, Loan, SearchResult

if TYPE_CHECKING:
    from ..plugin_api.models import AcquisitionOffer, AcquisitionOfferSet


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
    # Explicit legacy-compatible opt-in for acquisition during opening sync.
    # Presence of loans/digital_download or an ACSM URL alone is not consent.
    opening_loan_auto_acquisition: bool = False
    direct_download: bool = False
    personal_library: bool = False
    backup: bool = False
    return_loan: bool = False
    renew_loan: bool = False

    def names(self) -> list[str]:
        return [name for name, value in self.__dict__.items() if value]


@dataclass(frozen=True)
class ProviderSearchCapabilities:
    fields: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    pagination: Literal["unspecified", "none", "page", "next-link", "cursor"] = "unspecified"
    page_size: int | None = None
    max_pages: int | None = None
    notes: tuple[str, ...] = ()

    @property
    def declared(self) -> bool:
        return bool(self.fields or self.scopes or self.pagination != "unspecified" or self.page_size or self.max_pages or self.notes)

    def supports_field(self, field: str) -> bool:
        requested = str(field or "all").strip().casefold() or "all"
        return not self.fields or requested in {str(value).casefold() for value in self.fields}

    def supports_scope(self, scope: str) -> bool:
        requested = str(scope or "all").strip().casefold() or "all"
        return not self.scopes or requested in {str(value).casefold() for value in self.scopes}

    def summary(self) -> str:
        if not self.declared:
            return "Capacités de recherche non déclarées"
        parts: list[str] = []
        if self.fields:
            parts.append("Champs : " + ", ".join(self.fields))
        if self.scopes:
            parts.append("Périmètres : " + ", ".join(self.scopes))
        if self.pagination == "none":
            paging = "Pagination : non"
        elif self.pagination == "unspecified":
            paging = "Pagination : non déclarée"
        else:
            paging = "Pagination : " + self.pagination
        if self.page_size:
            paging += f" · {self.page_size}/page"
        if self.max_pages:
            paging += f" · max {self.max_pages} pages"
        parts.append(paging)
        return " · ".join(parts)


@dataclass(frozen=True)
class CredentialField:
    key: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""


@dataclass(frozen=True)
class AccountOptionField:
    """Provider-declared account setting; CoreUI treats its key as opaque data."""

    key: str
    label: str
    default: str = ""
    required: bool = False
    secret: bool = False


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
    search_capabilities: ProviderSearchCapabilities = field(default_factory=ProviderSearchCapabilities)
    # Interactive SSO requires a deliberate UI action; never start it from
    # the generic startup connection sweep or mark it connected by presence.
    interactive_authentication: bool = False
    # Optional declarative settings, backward-compatible with existing plugins.
    account_options: tuple[AccountOptionField, ...] = ()
    max_accounts_per_identity: int | None = None


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
    def search_capabilities(self) -> ProviderSearchCapabilities:
        return self.descriptor.search_capabilities

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

    def acquisition_offers(self, item: SearchResult) -> "AcquisitionOfferSet | list[AcquisitionOffer]":
        """Return explicit acquisition choices.

        Legacy providers may return a list. New providers can return the richer
        AcquisitionOfferSet with explicit format/offer preferences.
        """
        return []

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

