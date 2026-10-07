from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

# `provider` on legacy domain models is the upstream backend/service code
# (e.g. SYRACUSE/DILICOM). `source_provider` identifies the application
# provider plugin (e.g. paris, gallica, kobo).
Provider = str
Medium = Literal["digital", "physical", "unknown"]

@dataclass
class Account:
    display_name: str = ""
    barcode: str = ""
    unique_id: str = ""
    is_primary: bool = True
    provider_id: str = "paris"
    credential_id: str = "legacy"
    provider_label: str = "Bibliothèques de Paris"
    identity_id: str = "default"

@dataclass
class Loan:
    id: str
    provider: Provider
    medium: Medium
    record_id: str | None
    title: str
    thumbnail_url: str | None = None
    document_type: str | None = None
    due_at: str | None = None
    library: str | None = None
    barcode: str | None = None
    state: str | None = None
    is_late: bool = False
    is_soon_late: bool = False
    renewable: bool = False
    cannot_renew_reason: str | None = None
    can_return: bool = False
    access_medium: str | None = None
    has_been_renewed: bool = False
    ebook_url: str | None = None
    source_provider: str = "paris"
    source_credential: str = "legacy"
    # Stable upstream borrower identity. Empty string is a valid primary-account
    # identifier for Paris; None means the producer did not know the borrower.
    source_account_id: str | None = None
    identity_id: str | None = None
    author: str | None = None
    media_format: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass
class HistoryItem:
    id: str
    provider: Provider
    medium: Medium
    record_id: str | None
    title: str
    author: str | None = None
    year: str | None = None
    thumbnail_url: str | None = None
    default_thumbnail_url: str | None = None
    document_type: str | None = None
    borrowed_at: str | None = None
    returned_at: str | None = None
    source_provider: str = "paris"
    source_credential: str = "legacy"
    source_account_id: str | None = None
    identity_id: str | None = None

@dataclass
class Booking:
    id: str
    provider: Provider
    medium: Medium
    title: str
    record_id: str | None = None
    booking_date: str | None = None
    availability_date: str | None = None
    available_until: str | None = None
    location: str | None = None
    rank: str | None = None
    state: str | None = None
    source_provider: str = "paris"
    source_credential: str = "legacy"
    source_account_id: str | None = None
    identity_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass
class SearchResult:
    id: str
    record_id: str
    docbase: str
    medium: Medium
    title: str
    author: str | None
    type: str | None
    format: str | None
    publisher: str | None
    published_at: str | None
    description: str | None
    thumbnail_url: str | None
    friendly_url: str | None
    scenario: str
    source_provider: str = "paris"
    source_credential: str = "legacy"
    download_url: str | None = None
    content_type: str | None = None
    source_collection: str | None = None
    owned: bool | None = None
    downloadable: bool | None = None
    price: str | None = None
    currency: str | None = None
    revision_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

@dataclass
class Holding:
    id: str
    library: str | None
    section: str | None
    location: str | None
    call_number: str | None
    status: str
    status_label: str
    is_available: bool
    is_loanable: bool
    is_reservable: bool
    reservations: int
    due_at: str | None

@dataclass
class ItemDetail:
    record_id: str
    docbase: str
    title: str
    author: str | None
    type: str | None
    year: str | None
    thumbnail_url: str | None
    availability_status: str
    availability_label: str
    holdings: list[Holding]
    source_provider: str = "paris"
    source_credential: str = "legacy"


@dataclass
class BackupItem:
    provider_id: str
    credential_id: str
    item_id: str
    revision_id: str | None
    title: str
    author: str | None = None
    format: str | None = None
    local_path: str | None = None
    status: str = "pending"
    size: int | None = None
    sha256: str | None = None
    updated_at: int | None = None
    error: str | None = None
