"""#5225/#4496: a catalogue hint must never suppress an admitted loan GET.

This is positive presence evidence only, not a permission to download or
fulfill. The legacy Loan has no branch field: accept typed evidence only
when the exact current loan key resolves to one unambiguous branch/reference.
"""
from pathlib import Path
from time import monotonic

from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_recovery import _exact_borrower_bound_in_db
from .derived_variant_publication import _verified_variant_file
from .canonical_dedup_exclusions import ordinary_dedup_path_excluded


def _snapshot(storage, scope, edition, loan_id):
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("loan_presence_nested_transaction")
        with db:
            db.execute("BEGIN")
            args = (scope.identity_id, scope.provider_id, scope.credential_id,
                    scope.account_id, edition)
            known = db.execute(
                "SELECT 1 FROM source_references WHERE identity_id=? AND provider_id=? "
                "AND credential_id=? AND account_id=? AND edition_id=? LIMIT 1", args,
            ).fetchone()
            if known is None:
                return None
            sources = db.execute(
                "SELECT source_reference_id,branch_id,evidence_kind,evidence_ref "
                "FROM source_references WHERE identity_id=? AND provider_id=? "
                "AND credential_id=? AND account_id=? AND edition_id=? "
                "AND reference_kind='loan' AND right_id=? LIMIT 2", (*args, loan_id),
            ).fetchall()
            if (len(sources) != 1 or sources[0][2] != "provider_response"
                    or not sources[0][3] or not sources[0][1]
                    or not _exact_borrower_bound_in_db(db, scope)):
                return ()
            owners = db.execute(
                "SELECT directory_name FROM identities WHERE identity_id=?",
                (scope.identity_id,),
            ).fetchall()
            if len(owners) != 1:
                return ()
            directory = owners[0][0]
            if (type(directory) is not str or not directory or directory in (".", "..")
                    or "/" in directory or chr(92) in directory
                    or db.execute("SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
                                  (directory,)).fetchall() != [(scope.identity_id,)]):
                return ()
            copies = db.execute(
                "SELECT DISTINCT p.placement_id,p.path,p.artifact_id,a.sha256,a.size "
                "FROM placements p JOIN artifacts a ON a.artifact_id=p.artifact_id "
                "JOIN origin_links o ON o.identity_id=p.identity_id AND o.artifact_id=p.artifact_id "
                "WHERE p.identity_id=? AND p.state='present' AND o.origin_kind='provider' "
                "AND o.source_reference_id=? AND o.evidence_ref<>'' "
                "ORDER BY p.placement_id LIMIT 65",
                (scope.identity_id, sources[0][0]),
            ).fetchall()
            if not copies or len(copies) > 64:
                return ()
            return tuple(sources), directory, tuple(copies)


def canonical_loan_presence(service, loan, identity_id, *, inspector):
    """True = verified exact B; False = typed or unverifiable; None = no typed B.

    Preserve legacy completed receipts only when no typed coordinates exist.
    Do not use the catalogue's provider/item labels, path, or SHA alone.
    """
    storage = service.storage
    if not hasattr(storage, "_lock") or not callable(getattr(storage, "_connect", None)):
        return None
    try:
        def coordinates():
            return (
                loan.id, loan.record_id, loan.source_provider,
                loan.source_credential, loan.source_account_id,
                getattr(loan, "identity_id", None),
            )

        pinned = coordinates()
        loan_id, record, provider, credential, account, loan_identity = pinned
        edition = record or loan_id
        if (type(account) is not str or type(edition) is not str or not edition
                or type(loan_id) is not str or not loan_id
                or (loan_identity is not None and loan_identity != identity_id)):
            return False
        scope = ProviderRightsScope(identity_id, provider, credential, account)

        def source_current():
            return service.storage is storage and coordinates() == pinned

        evidence = _snapshot(storage, scope, edition, loan_id)
        if not source_current():
            return False
        if evidence is None:
            return None
        if not evidence:
            return False
        root = service.library_dir()
        if not isinstance(root, Path) or not root.is_absolute() or root == Path("/"):
            return False

        def current():
            return (source_current() and service.library_dir() == root
                    and source_current())

        if not current():
            return False
        _, directory, copies = evidence
        # Conflicting canonical artifacts are not a single exact delivery.
        if len({r[2] for r in copies}) != 1:
            return False
        remaining = 1024 ** 3
        deadline = monotonic() + 3
        for _placement, raw, artifact, digest, size in copies:
            if (type(raw) is not str or type(digest) is not str
                    or artifact != "sha256:" + digest or type(size) is not int
                    or not 0 < size <= remaining):
                continue
            path = Path(raw)
            owner = root / directory
            if (not path.is_absolute() or str(path) != raw
                    or not path.is_relative_to(owner) or path == owner
                    or any(part.startswith(".") for part in path.relative_to(owner).parts)
                    or ordinary_dedup_path_excluded(path, library_root=root,
                        identity_id=identity_id, owner_directory=directory)):
                continue
            if monotonic() >= deadline or not current():
                return False
            remaining -= size
            try:
                with _verified_variant_file(path, digest, expected_size=size) as recheck:
                    inspected = inspector(path)
                    # Unknown/non-boolean protection is not evidence of
                    # openable content. No mutable caller or new storage/root
                    # may inherit a positive local proof from this inspection.
                    if (getattr(inspected, "valid", None) is not True
                            or getattr(inspected, "protected", None) is not False):
                        continue
                    if not current() or _snapshot(storage, scope, edition, loan_id) != evidence:
                        return False
                    recheck()
                    return current()
            except (OSError, ValueError, RuntimeError, TypeError):
                continue
        return False
    except Exception:
        # A read failure is not positive reusable presence. The calling
        # admission, retry fences and provider rights remain mandatory.
        return False
