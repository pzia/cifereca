"""#6366: public C cover for an exact *provider-issued* borrower book record.

A source's private loan/history occurrence is never a public catalogue
observation or a canonical right. If it names the SAME provider-issued book
record as exactly one separately witnessed public catalogue source, the
cover-only, profile-independent canonical C may be used as a lower-priority
visual fallback. A title, author, ISBN, shared URL, cache HIT or provider name
alone cannot authorize it.

All SQLite reads happen on the already existing Core worker; GTK only
consumes an immutable receipt and rechecks its original borrower occurrence.
No provider I/O, entitlement mutation, EPUB scan or private information
is persisted into public C.
"""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from ..models import HistoryItem, Loan
from ..providers.base import ProviderDescriptor
from .source_context import source_context_epoch
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import is_canonical_cover_document
from .notice_c_exact_source_lookup import exact_public_source_c_inventory

_MAX_ITEMS = 2048
_BORROWER_PUBLIC_C_TOKEN = object()

def borrower_public_c_descriptors(service, *, source_epoch: int) -> tuple:
    """Read only installed registry declarations, never a provider query."""
    try:
        if type(source_epoch) is not int or source_context_epoch(service) != source_epoch:
            return ()
        descriptors = service.provider_descriptors()
        if (type(descriptors) not in (list, tuple) or len(descriptors) > 512
                or source_context_epoch(service) != source_epoch):
            return ()
        return tuple(descriptors)
    except (AttributeError, TypeError, ValueError, RuntimeError, LookupError):
        return ()


def _declared_branches(descriptors: object) -> dict:
    """Accept exact, unambiguous native declarations only."""
    if type(descriptors) not in (list, tuple) or len(descriptors) > 512:
        return {}
    result = {}
    seen = set()
    def valid(value):
        return (type(value) is str and 0 < len(value) <= 160
                and value == value.strip() and value.isprintable())
    for descriptor in descriptors:
        if type(descriptor) is not ProviderDescriptor or not valid(descriptor.id):
            continue
        provider = descriptor.id
        if provider in seen:
            result.pop(provider, None)
            continue
        seen.add(provider)
        rows = descriptor.borrower_public_edition_branches_v1
        if descriptor.state != "ready" or type(rows) is not tuple or len(rows) > 64:
            continue
        branches = {}
        for row in rows:
            if (type(row) is not tuple or len(row) != 4
                    or not all(valid(value) for value in row[:3])
                    or row[1] != "digital"
                    or (row[3] is not None and not valid(row[3]))
                    or row[:2] in branches):
                break
            branches[row[:2]] = row[2:]
        else:
            result[provider] = branches
    return result


def _attested_public_edition(item: Loan | HistoryItem, declarations: dict):
    """Return only an exact plugin-declared branch -> public edition."""
    source = declarations.get(item.source_provider, {}).get((item.provider, item.medium))
    if source is None:
        return None
    return (item.source_provider, item.record_id, item.medium, *source)


def _fold(value: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(c)
    ).strip()


def _coordinate(item: object, identity_id: str):
    if (type(item) not in (Loan, HistoryItem)
            or type(identity_id) is not str or not identity_id
            or type(item.identity_id) is not str
            or item.identity_id != identity_id
            or type(item.source_provider) is not str
            or not item.source_provider or len(item.source_provider) > 128
            or type(item.record_id) is not str
            or not 0 < len(item.record_id) <= 160
            or type(item.medium) is not str or item.medium != "digital"
            or type(item.provider) is not str or not item.provider
            or type(item.title) is not str or not item.title.strip()
            or (item.author is not None and type(item.author) is not str)
            or type(item.source_credential) is not str
            or not item.source_credential
            or type(item.id) is not str or not item.id
            # Unknown borrower is not the primary-account empty sentinel.
            # Native provider session readers must supply a pinned owner.
            or type(item.source_account_id) is not str
            or item.thumbnail_url is not None):
        return None
    # Only a provider branch with a proven Search mapping may link
    # a private occurrence to a public edition. The occurrence ID is
    # mandatory but does not itself identify any public book.
    return (item.source_provider, item.record_id, item.medium)


@dataclass(frozen=True, slots=True)
class VerifiedBorrowerPublicC:
    source_item: Loan | HistoryItem
    identity_id: str
    document: CoverDocumentScope
    epoch: int
    source_ref: str
    account_epoch: int
    source_epoch: int
    occurrence_id: str
    source_credential: str
    source_account_id: str | None
    provider_id: str
    record_id: str
    medium: str
    upstream_branch: str
    title: str
    author: str | None
    witness: object

    def current_for(self, source_item: object, *, identity_id: object) -> bool:
        """Pure GTK-safe validation; no SQLite and no rights transition."""
        return (
            type(self) is VerifiedBorrowerPublicC
            and self.witness is _BORROWER_PUBLIC_C_TOKEN
            and source_item is self.source_item
            and type(identity_id) is str and bool(identity_id)
            and identity_id == self.identity_id
            and _coordinate(source_item, identity_id) == (
                self.provider_id, self.record_id, self.medium,
            )
            and source_item.id == self.occurrence_id
            # DILICOM, SYRACUSE or another supplier branch can reuse an
            # upstream record string. A native branch switch must revoke C.
            and type(source_item.provider) is str
            and source_item.provider == self.upstream_branch
            and source_item.source_credential == self.source_credential
            and source_item.source_account_id == self.source_account_id
            and source_item.title == self.title
            and source_item.author == self.author
            and is_canonical_cover_document(self.document)
            and type(self.epoch) is int and self.epoch > 0
            and type(self.source_ref) is str
            and self.source_ref.startswith("notice-c:v1:")
        )


def attach_borrower_public_c(
    storage, rows: list, *, identity_id: str,
    account_epoch: int, source_epoch: int,
    descriptors: object = (),
) -> int:
    """Admit only native-branch-proven public editions in one bounded read.

    Private provider+record alone is insufficient: an explicit native
    branch contract must also match public docbase and edition revision.
    Preserve the unique strong relation > own C rule, and fail closed on
    contradictory strong edition relationships.
    """
    if (type(rows) is not list or not 0 < len(rows) <= _MAX_ITEMS
            or type(identity_id) is not str or not identity_id
            or type(account_epoch) is not int or account_epoch < 0
            or type(source_epoch) is not int or source_epoch < 0):
        return 0
    # Explicitly revoke an earlier worker receipt before re-indexing this
    # batch. A provider can reuse a mutable DTO across a newer inventory.
    for item in rows:
        if type(item) in (Loan, HistoryItem):
            item.__dict__.pop("_core_public_c_cover_receipt", None)
    declarations = _declared_branches(descriptors)
    wanted = {
        edition for item in rows
        if _coordinate(item, identity_id) is not None
        if (edition := _attested_public_edition(item, declarations)) is not None
    }
    if not wanted:
        return 0
    # The shared Core lookup resolves the complete public source coordinate
    # in a single SQLite snapshot. No global catalogue-size veto, separate
    # C scanner or provider dispatch is needed for a borrower inventory.
    resolved = exact_public_source_c_inventory(storage, [
        (provider, record, docbase, medium, revision)
        for provider, record, medium, docbase, revision in wanted
    ])
    admitted = {}
    for coords in wanted:
        provider, record, medium, docbase, revision = coords
        canonical = resolved.get((provider, record, docbase, medium, revision))
        # A retained exact public fiche remains useful even when the
        # same C has no cover. The existing borrower detail uses the
        # canonical reference for the independently validated web menu.
        # Do not promote an empty C with neither image nor web evidence.
        if canonical is None or not (canonical.candidates or canonical.catalogue_web_references):
            continue
        sources = [
            source for source in canonical.sources
            if source.provider_id == provider
            and source.public_bibliography is not None
            and (source.public_bibliography.record_id,
                 source.public_bibliography.medium,
                 source.public_bibliography.docbase,
                 source.public_bibliography.source_revision)
            == (record, medium, docbase, revision)
        ]
        if len(sources) == 1:
            admitted[coords] = canonical, sources[0]

    count = 0
    for item in rows:
        coords = _coordinate(item, identity_id)
        edition = (_attested_public_edition(item, declarations)
                   if coords is not None else None)
        if edition is None or edition not in admitted:
            continue
        c, source = admitted[edition]
        bibliography = source.public_bibliography
        # A contradictory provider source is not the same book even when
        # an upstream record ID was recycled. Text is NEGATIVE evidence only,
        # never used to discover a positive relation.
        if (_fold(item.title) != _fold(bibliography.title)
                or (item.author and bibliography.author
                    and _fold(item.author) != _fold(bibliography.author))):
            continue
        packet = VerifiedBorrowerPublicC(
            source_item=item, identity_id=identity_id,
            document=c.document, epoch=c.epoch,
            source_ref=source.source_ref,
            account_epoch=account_epoch, source_epoch=source_epoch,
            occurrence_id=item.id,
            source_credential=item.source_credential,
            source_account_id=item.source_account_id,
            provider_id=coords[0], record_id=coords[1], medium=coords[2],
            upstream_branch=item.provider,
            title=item.title, author=item.author,
            witness=_BORROWER_PUBLIC_C_TOKEN,
        )
        # The DTO is a disposable display/data object, not a rights row:
        # source facts are never copied to its private provider payload.
        item.__dict__["_core_public_c_cover_receipt"] = packet
        count += 1
    return count


def borrower_public_c_receipt(
    item: object, *, identity_id: object,
    account_epoch: object = None, source_epoch: object = None,
):
    """Return the Core-worker receipt only if still bound to this occurrence."""
    if type(item) not in (Loan, HistoryItem):
        return None
    receipt = item.__dict__.get("_core_public_c_cover_receipt")
    if (type(receipt) is VerifiedBorrowerPublicC
            and receipt.current_for(item, identity_id=identity_id)
            and (account_epoch is None or receipt.account_epoch == account_epoch)
            and (source_epoch is None or receipt.source_epoch == source_epoch)):
        return receipt
    return None


__all__ = [
    "VerifiedBorrowerPublicC", "attach_borrower_public_c",
    "borrower_public_c_receipt", "borrower_public_c_descriptors",
]
