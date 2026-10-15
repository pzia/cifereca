"""Kobo #1345: privacy-safe, non-executable manual reconciliation plan.

Consumes ONLY the already redacted Kobo audit report. Never accepts a file,
path, credential, entitlement or product ID and has no service/storage handle.
A SHA coincidence is evidence for review, not proof of DRM rights or permission
to delete an original. Plans cannot be replayed as mutation commands.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_AUDIT_SCHEMA = "kobo.local.reconciliation.audit/1"
_ALLOWED_SCOPES = frozenset({"single_account", "all_configured"})
# Codes are fixed and never interpolate untrusted provider metadata.
_MANUAL_CHECKS = {
    "confirmed_binary_match": (
        "verify_same_identity_and_account",
        "verify_sha_size_and_provenance",
        "verify_entitlements_and_distinct_placements",
        "retain_all_originals",
    ),
    "same_product_different_bytes": (
        "verify_same_identity_and_account",
        "compare_edition_and_revision_without_filename_matching",
        "verify_separate_rights_and_provenance",
        "retain_all_originals",
    ),
    "active_entitlement_without_verified_artifact": (
        "verify_account_and_active_entitlement",
        "inspect_local_ledger_and_permissions",
        "inspect_artifact_without_automatic_download",
        "retain_all_originals",
    ),
    "stored_hash_mismatch": (
        "revalidate_original_file_integrity",
        "compare_persisted_ledger_and_source_provenance",
        "quarantine_for_manual_review_without_mutation",
        "retain_all_originals",
    ),
}
_MAX_CASES = 24
_MAX_ACCOUNTS = 4096
_MAX_COUNT = 1_000_000


def _count(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_COUNT


def _valid_audit(audit: object) -> Mapping[str, Any]:
    if not isinstance(audit, Mapping):
        raise ValueError("invalid_kobo_audit_report")
    if (
        audit.get("schema") != _AUDIT_SCHEMA
        or type(audit.get("scope")) is not str
        or audit.get("scope") not in _ALLOWED_SCOPES
        or audit.get("offline") is not True
        or audit.get("read_only") is not True
        or audit.get("dry_run") is not True
        or audit.get("downloads_authorized") is not False
        or audit.get("mutations_authorized") is not False
        or type(audit.get("truncated")) is not bool
    ):
        raise ValueError("invalid_kobo_audit_report")
    counts = audit.get("counts")
    if not isinstance(counts, Mapping) or not counts or any(
        key not in {
            "backup_rows", "linked_files_verified", "unverified_placements",
            "missing_or_unlinked", "stored_hash_mismatch",
            "unproven_catalogue_links", "same_product_different_bytes",
            "binary_match_groups", "active_entitlements_unmaterialized",
        } or not _count(value)
        for key, value in counts.items()
    ):
        raise ValueError("invalid_kobo_audit_report")
    cases = audit.get("cases")
    accounts = audit.get("accounts")
    if (
        not isinstance(cases, list) or len(cases) > _MAX_CASES
        or not isinstance(accounts, list) or len(accounts) > _MAX_ACCOUNTS
    ):
        raise ValueError("invalid_kobo_audit_report")
    for index, account in enumerate(accounts, start=1):
        if (
            not isinstance(account, Mapping)
            or account.get("account") != f"account-{index}"
            or not isinstance(account.get("counts"), Mapping)
            or any(key not in counts or not _count(value)
                   for key, value in account["counts"].items())
        ):
            raise ValueError("invalid_kobo_audit_report")
    for index, case in enumerate(cases, start=1):
        if (
            not isinstance(case, Mapping)
            or case.get("case") != f"case-{index:03d}"
            or type(case.get("category")) is not str
            or case.get("category") not in _MANUAL_CHECKS
            or not _count(case.get("artifacts"))
            or case.get("decision") != "manual_review_only"
        ):
            raise ValueError("invalid_kobo_audit_report")
    return audit


def kobo_manual_reconciliation_plan(audit: object) -> dict[str, Any]:
    """Prepare checks, never executable commands, from a validated local audit.

    Cases are ephemeral *within that one audit*: no account, filename, ISBN,
    product_id, private SHA or stable mutation target can be reconstructed.
    """
    report = _valid_audit(audit)
    cases = [
        {
            "case": f"case-{index:03d}",
            "category": row["category"],
            "artifacts": row["artifacts"],
            "status": "manual_review_required",
            "checks": list(_MANUAL_CHECKS[row["category"]]),
            "approved_actions": [],
        }
        for index, row in enumerate(report["cases"], start=1)
    ]
    # Missing, unlinked, wrong-hash and unproven catalogue links may lack
    # individual cases; the audit intentionally exposes only bounded totals.
    totals = report["counts"]
    unresolved = [
        category for category in (
            "unverified_placements", "missing_or_unlinked",
            "unproven_catalogue_links",
        ) if totals.get(category, 0)
    ]
    if report["truncated"]:
        unresolved.append("audit_budget_exhausted")
    return {
        "schema": "kobo.local.reconciliation.manual-plan/1",
        "source_schema": _AUDIT_SCHEMA,
        "scope": report["scope"],
        "account_count": len(report["accounts"]),
        "evidence_complete": not report["truncated"],
        "review_status": (
            "incomplete_audit" if report["truncated"]
            else "manual_review_required" if cases or unresolved
            else "no_review_case_reported"
        ),
        "case_count": len(cases),
        "cases": cases,
        "unresolved_evidence_codes": unresolved,
        "requires_explicit_human_validation": True,
        "revalidate_all_evidence_before_any_future_mutation": True,
        "retention_policy": "preserve_original_files",
        "write_authorized": False,
        "delete_authorized": False,
        "download_authorized": False,
        "automatic_reconciliation_authorized": False,
        "execution_steps": [],
        "limitations": [
            "case_ids_are_ephemeral_and_not_storage_references",
            "binary_hash_match_does_not_prove_ownership_or_edition",
            "rights_and_file_integrity_require_independent_validation",
            "dry_run_does_not_authorize_any_mutation",
        ],
    }


__all__ = ["kobo_manual_reconciliation_plan"]
