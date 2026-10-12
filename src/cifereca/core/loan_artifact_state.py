"""Read-only classification of the prior local artifact for an automatic loan.

This boundary must not write to the acquisition ledger, change the catalog,
contact a provider, or attempt ADEPT fulfillment. The caller keeps the
per-loan locks and all mutations. Artifact inspection is injected so the
historical cifereca.services.inspect_artifact monkeypatch remains effective.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


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
    source_provider: str,
    artifact_inspector: Callable[..., Any],
) -> PreviousLoanArtifact:
    """Preserve the historical exact ledger scope and local-file predicates.

    A valid non-protected file is reusable only after an acquisition marked
    complete; ADEPT acquisitions additionally require post_processed. Legacy
    ADEPT artifacts may need a repair, not an automatic second fulfillment.
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
    if previous_path is not None and previous_path.is_file():
        expected_previous = "pdf" if previous_path.suffix.casefold() == ".pdf" else "epub" if previous_path.suffix.casefold() == ".epub" else None
        previous_inspection = artifact_inspector(previous_path, expected_previous)
    legacy_acsm_artifact = bool(
        source_provider == "paris"
        and (previous or {}).get("executor") == "kobo-adept"
        and previous_path is not None
        and previous_path.is_file()
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
        and previous_path.is_file()
        and previous_inspection is not None
        and previous_inspection.valid
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
