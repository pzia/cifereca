"""#976: historical ADEPT evidence and local placement are separate facts.

No provider or runtime call, return, re-fulfillment, account binding or right.
A receipt is opaque: never infer the actual Adobe activation profile from a
borrower's name, a same-SHA placement or the current configuration.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .canonical_publication import _stable
from .canonical_recovery import _scope
from .derived_variant_publication import (
    DerivedPublicationConflict, _verified_variant_file,
)
from .adept_historical_snapshot import historical_read_snapshot
from .source_convergence import SourceKey

_MAX_BYTES = 1024 ** 3


def _text(value, maximum=1024):
    return (type(value) is str and 0 < len(value) <= maximum
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def _snapshot(storage, source, placement_id, adept_receipt_ref):
    coords = _scope(source)
    identity = coords[0]
    source_id = _stable("source", *coords)
    with historical_read_snapshot(storage) as db:
        reference = db.execute(
            "SELECT identity_id,provider_id,credential_id,account_id,branch_id,"
            "edition_id,right_id,reference_kind FROM source_references "
            "WHERE source_reference_id=?", (source_id,),
        ).fetchone()
        if reference != tuple(coords):
            return None
        links = db.execute(
            "SELECT artifact_id FROM origin_links WHERE origin_kind='adept' "
            "AND identity_id=? AND source_reference_id=? AND evidence_ref=? LIMIT 2",
            (identity, source_id, adept_receipt_ref),
        ).fetchall()
        if len(links) != 1:
            return None
        artifact_id = links[0][0]
        artifact = db.execute(
            "SELECT sha256,size FROM artifacts WHERE artifact_id=?",
            (artifact_id,),
        ).fetchone()
        if (artifact is None or not _text(artifact[0], 64)
                or len(artifact[0]) != 64
                or any(ch not in "0123456789abcdef" for ch in artifact[0])
                or artifact_id != "sha256:" + artifact[0]
                or type(artifact[1]) is not int or artifact[1] <= 0):
            return None
        # A provider delivery OriginLink is independent of an ADEPT receipt.
        # This does not attest current rights or connect the receipt to a path.
        provider = bool(db.execute(
            "SELECT 1 FROM origin_links WHERE origin_kind='provider' "
            "AND identity_id=? AND source_reference_id=? AND artifact_id=? LIMIT 1",
            (identity, source_id, artifact_id),
        ).fetchone())
        profile = db.execute(
            "SELECT directory_name FROM identities WHERE identity_id=? LIMIT 2",
            (identity,),
        ).fetchall()
        directory = profile[0][0] if len(profile) == 1 else None
        if (not _text(directory) or directory in (".", "..")
                or "/" in directory or chr(92) in directory
                or db.execute(
                    "SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
                    (directory,),
                ).fetchall() != [(identity,)]):
            directory = None
        placement = db.execute(
            "SELECT artifact_id,identity_id,path,state FROM placements WHERE placement_id=?",
            (placement_id,),
        ).fetchone()
        if (placement is None or placement[:2] != (artifact_id, identity)
                or not _text(placement[2], 4096)):
            placement = None
        return artifact_id, artifact, provider, directory, placement


def review_historical_adept_evidence(
    storage, *, source: SourceKey, placement_id: str, adept_receipt_ref: str,
    library_root: Path,
):
    """Review an exact known source and opaque receipt, not search by name.

    Only an already scoped Core diagnostic may supply these coordinates.
    Current connection or Adobe binding is deliberately not substituted for
    historical provenance. The result contains no private identifiers.
    """
    def report(origin="unverified", provider=False, placement="not_checked"):
        return {
            "adept_origin": origin,
            "provider_origin_recorded": provider,
            "placement_evidence": placement,
            "activation_profile": "unknown",
            "provider_loan_state": "not_checked",
            "adept_license_state": "not_checked",
            "receipt_attests_exact_placement": False,
            "may_rebind_or_return": False,
            "may_reacquire": False,
        }

    if (type(source) is not SourceKey or source.kind != "loan"
            or not _text(placement_id) or not _text(adept_receipt_ref)
            or not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts):
        return report()
    try:
        observed = _snapshot(storage, source, placement_id, adept_receipt_ref)
        if observed is None:
            return report()
        _artifact_id, (digest, size), provider, directory, placement = observed
        if directory is None or placement is None:
            return report("recorded_exact_source", provider, "scope_unverified")
        path = Path(placement[2])
        profile = library_root / directory
        if (not path.is_absolute() or ".." in path.parts or str(path) != placement[2]
                or path == profile or not path.is_relative_to(profile)):
            return report("recorded_exact_source", provider, "scope_unverified")
        if placement[3] != "present":
            return report("recorded_exact_source", provider, "not_recorded_present")
        if size > _MAX_BYTES:
            return report("recorded_exact_source", provider, "review_budget_exceeded")
        try:
            with _verified_variant_file(path, digest, expected_size=size) as current:
                signature = current()
                if _snapshot(storage, source, placement_id, adept_receipt_ref) != observed:
                    return report()
                if current() != signature:
                    return report()
        except (DerivedPublicationConflict, OSError):
            return report("recorded_exact_source", provider, "bytes_unverified")
        # Same identity + same artifact + verified bytes is not a
        # transaction-specific proof that this file came from this receipt.
        return report("recorded_exact_source", provider, "verified_artifact_bytes")
    except (sqlite3.Error, ValueError, TypeError, RuntimeError, RecursionError, OSError):
        return report()
