"""Read-only classification of the prior local artifact for an automatic loan.

This boundary must not write to the acquisition ledger, change the catalog,
contact a provider, or attempt ADEPT fulfillment. The caller keeps the
per-loan locks and all mutations. Artifact inspection is injected so the
historical cifereca.services.inspect_artifact monkeypatch remains effective.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Iterable, Mapping

from .loan_provider_delivery import _physical_witness
from .derived_variant_publication import _verified_variant_file


@dataclass(frozen=True, slots=True)
class PreviousLoanArtifact:
    row: Mapping[str, Any] | None
    metadata: dict[str, Any]
    path: Path | None
    inspection: Any
    legacy_acsm_artifact: bool
    complete_local: bool


def inspect_previous_loan_artifact(
    acquisitions: Iterable[Mapping[str, Any]],
    *,
    identity_id: str,
    credential_id: str,
    account_id: str | None,
    item_id: str,
    artifact_inspector: Callable[..., Any],
) -> PreviousLoanArtifact:
    """Preserve the historical exact ledger scope and local-file predicates.

    A valid non-protected file is reusable only after an acquisition marked
    complete; ADEPT acquisitions additionally require post_processed. Legacy
    ADEPT artifacts may need a repair, not an automatic second fulfillment.
    This is an executor/artifact fact, independent of the source provider.
    """
    matches = [
        row for row in acquisitions
        if row.get("identity_id") == identity_id
        and row.get("credential_id") == credential_id
        and str(row.get("account_id") or "") == str(account_id or "")
        and str(row.get("item_id") or "") == item_id
        and row.get("strategy") == "loan_auto"
    ]
    previous = matches[0] if matches else None
    previous_meta = dict((previous or {}).get("metadata") or {})
    previous_path = Path(str(previous_meta.get("path") or "")) if previous_meta.get("path") else None
    previous_inspection = None
    physical = False
    if previous_path is not None:
        expected_previous = "pdf" if previous_path.suffix.casefold() == ".pdf" else "epub" if previous_path.suffix.casefold() == ".epub" else None
        artifact_id = (previous or {}).get("artifact_id")
        # Historical path-only receipts retain their compatibility. But
        # an explicit content-addressed receipt cannot attest different
        # bytes merely because both files are structurally valid books.
        try:
            with _physical_witness(previous_path, ValueError) as (size, current):
                if artifact_id is None:
                    previous_inspection = artifact_inspector(previous_path, expected_previous)
                    current()
                    physical = True
                elif (type(artifact_id) is str
                      and re.fullmatch(r"sha256:[0-9a-f]{64}", artifact_id)
                      and 0 < size <= 1024 ** 3):
                    with _verified_variant_file(previous_path, artifact_id[7:],
                                                expected_size=size) as recheck:
                        previous_inspection = artifact_inspector(previous_path, expected_previous)
                        recheck()
                        current()
                        physical = True
        except (OSError, ValueError, TypeError, RuntimeError):
            previous_inspection = None
        if not physical:
            previous_inspection = None
    legacy_acsm_artifact = bool(
        (previous or {}).get("executor") == "kobo-adept"
        and previous_path is not None
        and physical
        and (
            not previous_meta.get("post_processed")
            or previous_inspection is None
            or not previous_inspection.valid
            or previous_inspection.protected is True
        )
    )
    previous_complete_local = bool(
        previous
        and previous.get("status") == "complete"
        and previous_path is not None
        and physical
        and previous_inspection is not None
        and previous_inspection.valid is True
        and previous_inspection.protected is not True
        and (
            (previous or {}).get("executor") != "kobo-adept"
            or bool(previous_meta.get("post_processed"))
        )
    )
    return PreviousLoanArtifact(
        previous, previous_meta, previous_path, previous_inspection,
        legacy_acsm_artifact, previous_complete_local,
    )


def local_catalog_candidates(
    entries: Iterable[Mapping[str, Any]],
    *,
    identity_id: str,
    source_provider: str,
    item_id: str,
) -> list[Mapping[str, Any]]:
    """Select existing local rows without inferring ownership from a filename.

    Preserve the original catalog row order and match all three dimensions:
    destination identity, source provider and provider item ID. The caller
    still validates every matching artifact before trusting this projection.
    """
    return [
        row for row in entries
        if row.get("identity_id") == identity_id
        and row.get("provider_id") == source_provider
        and str(row.get("item_id") or "") == item_id
        and row.get("local_state") == "local"
    ]


def inspect_catalog_local_file(
    entry: Mapping[str, Any],
    *,
    artifact_inspector: Callable[..., Any],
) -> tuple[bool, Path | None]:
    """Check one local projection without changing storage or the file.

    Return (usable, path). Only a valid artifact whose protected flag is not
    explicitly True may be reused. Inject the service's inspector so existing
    monkeypatch and test seams remain active. The caller owns the original
    missing/invalid persistence and tracing under its existing locks.
    """
    path_text = str((entry.get("metadata") or {}).get("path") or "")
    path = Path(path_text) if path_text else None
    if path is not None and path.is_file():
        inspection = artifact_inspector(path)
        if inspection.valid and inspection.protected is not True:
            return True, path
    return False, path
