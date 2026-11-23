"""#7092 public contextual known-C review; no rights, private paths or requests."""
from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LocalOriginDetailMatch:
    provider_id: str
    record_id: str
    docbase: str
    edition_id: str
    title: str
    author: str | None
    comparison: str
    stale: bool
    observed_at: int


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginPublicChoice:
    token: str
    provider_id: str


@dataclass(frozen=True, slots=True)
class LocalOriginPublicMatch:
    provider_id: str
    record_id: str
    docbase: str
    medium: str
    title: str
    author: str | None
    media_format: str | None
    isbns: tuple[str, ...]
    comparison: str


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginPublicReport:
    provider_id: str
    status: str
    matches: tuple[LocalOriginPublicMatch, ...]
    provider_access_performed: bool
    coverage_complete: bool = False
    fresh_network_verified: bool = False
    association_authorized: bool = False
    # Qualification of the one response, independent of catalogue coverage.
    window_status: str = "unverified"


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginPrivateChoice:
    token: str
    provider_id: str
    credential_id: str
    account_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class LocalOriginPrivateMatch:
    edition_id: str
    title: str
    author: str | None
    comparison: str
    evidence: str
    observed_at: int | None
    branch: str = ""
    resource_id: str = ""
    declaration_choice: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginPrivateReport:
    status: str
    provider_id: str
    credential_id: str
    account_id: str
    kind: str
    matches: tuple[LocalOriginPrivateMatch, ...]
    window_truncated: bool
    coverage_complete: bool = False
    fresh_network_verified: bool = False
    provider_access_performed: bool = False
    association_authorized: bool = False
    page: int = 1
    next_token: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginRemoteLoanChoice:
    token: str
    provider_id: str
    credential_id: str
    account_id: str


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginRemoteLoanMatch:
    edition_id: str | None
    loan_id: str
    title: str
    author: str | None
    comparison: str


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginRemoteLoanReport:
    status: str
    provider_id: str
    credential_id: str
    account_id: str
    matches: tuple[LocalOriginRemoteLoanMatch, ...]
    window_truncated: bool
    observed_at: str | None
    provider_read_requested: bool
    coverage_complete: bool = False
    fresh_network_verified: bool = False
    association_authorized: bool = False
    local_description_transmitted: bool = False


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginDetailPage:
    token: str
    page: int
    matches: tuple[LocalOriginDetailMatch, ...]
    known_matches: int
    has_more: bool
    can_continue: bool
    status: str
    metadata_evidence: str
    coverage_complete: bool = False
    provider_access_performed: bool = False
    association_authorized: bool = False
    public_choices: tuple[LocalOriginPublicChoice, ...] = ()
    private_choices: tuple[LocalOriginPrivateChoice, ...] = ()
    remote_loan_choices: tuple[LocalOriginRemoteLoanChoice, ...] = ()


@runtime_checkable
class LocalOriginDetailReadBroker(Protocol):
    """Worker-only exact local review, one transient view per activation.

    The host supplies a revocable view guard. Page tokens only continue
    cache review. Separate public-choice tokens require explicit consent to
    send the verified OPF title to that anonymous catalogue, once per token.
    Private comparisons may return a one-use next_token for the same cached
    catalogue; each continuation requires a separate explicit confirmation.
    """
    def preview(self, *, identity_id: str, placement_id: str,
                still_current: Callable[[], bool], confirmed: bool = False,
                page_size: int = 20) -> LocalOriginDetailPage | None: ...
    def next_page(self, *, token: str, confirmed: bool = False) -> LocalOriginDetailPage | None: ...
    def search_public(self, *, token: str, confirmed: bool = False) -> LocalOriginPublicReport | None: ...
    def compare_private(self, *, token: str, confirmed: bool = False) -> LocalOriginPrivateReport | None: ...
    def cancel(self, *, token: str) -> None: ...


@runtime_checkable
class LocalOriginRemoteLoanReadBroker(LocalOriginDetailReadBroker, Protocol):
    """Optional typed private inventory comparison; no local title upload."""
    def compare_remote_loan(self, *, token: str,
                            confirmed: bool = False) -> LocalOriginRemoteLoanReport | None: ...


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginDeclarationAdvice:
    """Preview only; no command, raw path, writer token or rights authority."""
    provider_id: str
    credential_id: str
    account_id: str
    edition_id: str
    loan_id: str
    trust: str = "user_declaration"
    association_authorized: bool = False


@runtime_checkable
class LocalOriginDeclarationReviewBroker(LocalOriginDetailReadBroker, Protocol):
    def preview_loan_declaration(self, *, token: str,
                                 confirmed: bool = False) -> LocalOriginDeclarationAdvice | None: ...


@dataclass(frozen=True, slots=True, repr=False)
class LocalOriginPurchaseDeclarationAdvice:
    provider_id: str
    credential_id: str
    account_id: str
    edition_id: str
    purchase_id: str
    trust: str = "user_declaration"
    association_authorized: bool = False


@runtime_checkable
class LocalOriginPurchaseDeclarationReviewBroker(LocalOriginDetailReadBroker, Protocol):
    def preview_purchase_declaration(self, *, token: str,
                                     confirmed: bool = False) -> LocalOriginPurchaseDeclarationAdvice | None: ...
