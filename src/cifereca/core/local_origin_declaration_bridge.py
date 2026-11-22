"""#4030: explicit private loan comparison -> existing #2797 declaration.

Core-only operator boundary. No plugin mutation API, no new writer or rights.
A private documentary match is not a legal right: require the exact existing
selected entitlement and the same attested operational source occurrence.
"""
from dataclasses import dataclass
import os
from threading import Lock

from ..epub_metadata import inspect_epub
from .canonical_publication import _stable
from .derived_variant_publication import _file_signature
from .library_declared_links import (
    declare_local_link, _admit_declaration_command,
)
from .library_inventory import FileObservation, _fingerprint, _open_root
from .library_sqlite_freshness import sqlite_source_stamp
from .library_structure import inspect_stream_structure
from .local_origin_private_catalogue import (
    PrivateCatalogueChoice, PrivateDocumentaryMatch, compare_known_private_catalogue,
    _binding,
)
from .local_origin_suggestion_preview import _verified_candidate

_MAX_BYTES = 512 * 1024 * 1024


def _right(db, source):
    scope = source.scope
    coords = (scope.identity_id, scope.provider_id, scope.credential_id,
              scope.account_id, source.branch, source.edition_id,
              source.resource_id, source.kind)
    ref = db.execute(
        "SELECT identity_id,provider_id,credential_id,account_id,branch_id,"
        "edition_id,right_id,reference_kind,state,evidence_kind,evidence_ref,observed_at "
        "FROM source_references WHERE source_reference_id=?",
        (_stable("source", *coords),),
    ).fetchone()
    if (ref is None or ref[:10] != (*coords, "active", "provider_response")
            or type(ref[10]) is not str or not ref[10]):
        return None
    # The existing declaration writer selects a single entitlement by item.
    # Never choose one loan amongst multiple cycles on that same edition,
    # and never manufacture an entitlement out of a SourceReference.
    rights = db.execute(
        "SELECT entitlement_id,entitlement_type,state,loan_id,expires_at "
        "FROM entitlements WHERE identity_id=? AND provider_id=? AND credential_id=? "
        "AND account_id=? AND item_id=? LIMIT 2",
        (*coords[:4], source.edition_id),
    ).fetchall()
    if (len(rights) != 1 or type(rights[0][0]) is not str or not rights[0][0]):
        return None
    if source.kind == "loan":
        if rights[0][1:4] != ("loan", "active", source.resource_id):
            return None
    elif source.kind == "purchase":
        # A legacy owned entitlement is edition-scoped: accept only one
        # exact observed purchase occurrence across all branches of this
        # account, never arbitrate multiple purchases by description.
        if rights[0][1:3] != ("owned", "active") or rights[0][3] not in (None, ""):
            return None
        occurrences = db.execute(
            "SELECT branch_id,right_id FROM source_references WHERE identity_id=? "
            "AND provider_id=? AND credential_id=? AND account_id=? AND edition_id=? "
            "AND reference_kind='purchase' LIMIT 2",
            (*coords[:4], source.edition_id),
        ).fetchall()
        if occurrences != [(source.branch, source.resource_id)]:
            return None
    else:
        return None
    return ref, rights[0]


def _observation(path, local, signature, identity_id, live):
    if (local[6] != "epub" or path.suffix.casefold() != ".epub"
            or not 0 < local[4] <= _MAX_BYTES):
        return None
    folder = _open_root(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW |
                     os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=folder)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if _file_signature(before) != signature or not live():
                return None
            if inspect_epub(stream, metadata_only=True)["epub_status"] != "ok":
                return None
            if inspect_stream_structure(stream, "epub", max_bytes=_MAX_BYTES,
                                        cancelled=lambda: not live()) != "epub_structure_crc_ok":
                return None
            if (_file_signature(os.fstat(stream.fileno())) != signature
                    or _file_signature(os.stat(path.name, dir_fd=folder,
                                               follow_symlinks=False)) != signature):
                return None
            return FileObservation(identity_id, str(path), local[4], "epub",
                                   local[3], "ok", fingerprint=_fingerprint(before),
                                   structure_validation="epub_structure_crc_ok")
    finally:
        os.close(folder)


@dataclass(frozen=True, slots=True, repr=False)
class _DeclarationPreview:
    service: object
    choice: PrivateCatalogueChoice
    candidate: PrivateDocumentaryMatch
    continuation: object
    stamp: tuple
    right: tuple
    observation: FileObservation
    token: str

    @property
    def context(self):
        source = self.candidate.source
        scope = source.scope
        return {"identity": scope.identity_id, "provider": scope.provider_id,
                "account": scope.account_id, "edition": source.edition_id,
                "loan": source.resource_id if source.kind == "loan" else None,
                "purchase": source.resource_id if source.kind == "purchase" else None,
                "path": self.observation.path,
                "trust": "user_declaration", "rights_mutations": 0,
                "placement_mutations": 0, "file_mutations": 0}


def _options(candidate, observation):
    source, scope = candidate.source, candidate.source.scope
    return dict(observation=observation, identity_id=scope.identity_id,
                provider_id=scope.provider_id, credential_id=scope.credential_id,
                account_id=scope.account_id, item_id=source.edition_id)


def _preview_private_declaration(service, *, choice, candidate, kind,
                                 continuation=None, confirmed=False,
                                 still_current=lambda: True):
    """Explicit separate selection of a returned loan, never a title-to-right join.

    The existing comparison reader repeats the exact page before the existing
    declaration authority supplies its private diff. No arbitrary URL, path,
    right ID or callback can substitute for the chosen candidate.
    """
    if (confirmed is not True or type(choice) is not PrivateCatalogueChoice
            or choice.kind != kind or type(candidate) is not PrivateDocumentaryMatch
            or candidate.source.kind != kind or candidate.source.scope != choice.scope
            or candidate.comparison == "purchase_description_conflicting"
            or choice.request.service is not service or not callable(still_current)):
        return None
    request = choice.request
    storage = request.storage

    def live():
        binding = _binding(service, request, choice.account, choice.scope)
        return (still_current() is True and binding is not None
                and all(a is b for a, b in zip(choice.binding, binding)))

    try:
        if not live():
            return None
        stamp = sqlite_source_stamp(request.database)
        if stamp is None:
            return None
        result = compare_known_private_catalogue(
            service, request, account=choice.account, kind=kind, choice=choice,
            continuation=continuation, confirmed=True, still_current=live)
        if result["status"] != "partial" or result["candidates"].count(candidate) != 1:
            return None
        with _verified_candidate(storage, library_root=request.root,
                                 identity_id=request.identity_id, placement_id=request.placement_id,
                                 catalogue=True) as (local, path, signature):
            if (local != choice.evidence.evidence or signature != choice.evidence.signature
                    or not live()):
                return None
            observation = _observation(path, local, signature, request.identity_id, live)
            if observation is None:
                return None
        with storage._lock:
            db = storage._connect()
            if db.in_transaction:
                return None
            with db:
                db.execute("BEGIN")
                right = _right(db, candidate.source)
        if right is None or not live() or sqlite_source_stamp(request.database) != stamp:
            return None
        preview = declare_local_link(service, **_options(candidate, observation),
                                     cancelled=lambda: still_current() is not True)
        if (preview.status != "preview" or preview.plan_token is None or not live()
                or sqlite_source_stamp(request.database) != stamp):
            return None
        return _DeclarationPreview(service, choice, candidate, continuation, stamp, right,
                                   observation, preview.plan_token)
    except Exception:
        return None


def preview_private_loan_declaration(service, **options):
    return _preview_private_declaration(service, kind="loan", **options)


def preview_private_purchase_declaration(service, **options):
    return _preview_private_declaration(service, kind="purchase", **options)


def _admit_private_declaration(service, preview, *, kind, user_confirmed=False,
                               still_current=lambda: True):
    """Consume an exact refreshed preview through the existing single writer.

    Worker-only admission: the repeated bounded SHA/structure reads must not
    run on GTK. This admits a one-shot Core command, not a provider download. The existing
    authority owns the acquisition lock, FULL transaction, exact diff token,
    file SHA recheck and historical revocation. No new persistent namespace.
    """
    if (user_confirmed is not True or type(preview) is not _DeclarationPreview
            or preview.service is not service or preview.choice.kind != kind
            or not callable(still_current)):
        return None
    request = preview.choice.request
    try:
        if still_current() is not True or sqlite_source_stamp(request.database) != preview.stamp:
            return None
        fresh = _preview_private_declaration(
            service, kind=kind, choice=preview.choice, candidate=preview.candidate,
            continuation=preview.continuation, confirmed=True, still_current=still_current)
        if fresh != preview:
            return None
        command = _admit_declaration_command(
            service, identity_id=request.identity_id,
            options=_options(preview.candidate, preview.observation),
            confirmation=preview.token,
        )
        if sqlite_source_stamp(request.database) != preview.stamp:
            return None
    except Exception:
        return None
    lock, used = Lock(), False

    def work():
        nonlocal used
        with lock:
            if used:
                return None
            used = True
        try:
            binding = _binding(service, request, preview.choice.account, preview.choice.scope)
            if (still_current() is not True or binding is None
                    or not all(a is b for a, b in zip(preview.choice.binding, binding))):
                return None
            if sqlite_source_stamp(request.database) != preview.stamp:
                return None
            return command()
        except Exception:
            return None

    return work


def admit_private_loan_declaration(service, preview, **options):
    return _admit_private_declaration(service, preview, kind="loan", **options)


def admit_private_purchase_declaration(service, preview, **options):
    return _admit_private_declaration(service, preview, kind="purchase", **options)
