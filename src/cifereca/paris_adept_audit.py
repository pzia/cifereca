"""Read-only evidence report for Paris loans appearing under multiple connections.

This is an offline reconciliation aid, not an Adobe ownership verifier.
No provider session, fulfillment, retry or mutation is ever performed here.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def build_paris_adept_audit(storage: Any, *, credential_id: str | None = None) -> dict[str, Any]:
    """Cross-reference active Paris entitlements, acquisitions and pinned grants."""
    owners = {
        str(row["credential_id"]): str(row["identity_id"])
        for row in storage.provider_connections()
        if str(row.get("provider_id") or "").lower() == "paris"
    }
    grants = {
        (str(row["credential_id"]), str(row["account_id"])): row
        for row in storage.paris_borrower_adept_bindings()
    }
    exposures: dict[str, dict[tuple[str, str | None, str, str], dict[str, Any]]] = defaultdict(dict)
    rows_without_loan_id = 0

    def add(row: dict[str, Any], loan_id: str, *, source: str) -> None:
        nonlocal rows_without_loan_id
        loan_key = str(loan_id or "").strip()
        if not loan_key:
            rows_without_loan_id += 1
            return
        credential = str(row.get("credential_id") or "")
        account_raw = row.get("account_id")
        account = None if account_raw is None else str(account_raw)
        identity = str(row.get("identity_id") or "")
        item = str(row.get("item_id") or "")
        key = (credential, account, identity, item)
        current = exposures[loan_key].get(key)
        if current is None:
            current = {
                "credential_id": credential, "account_id": account,
                "connection_identity_id": identity, "item_id": item,
                "sources": [],
            }
            exposures[loan_key][key] = current
        if source not in current["sources"]:
            current["sources"].append(source)
        if source == "acquisition":
            current["acquisition_status"] = str(row.get("status") or "")
            ade = ((row.get("metadata") or {}).get("adept_binding") or {})
            if isinstance(ade, dict) and ade.get("adept_profile_id"):
                current["recorded_adept_profile_id"] = str(ade["adept_profile_id"])

    for row in storage.entitlements(provider_id="paris", state="active"):
        add(row, row.get("loan_id"), source="entitlement")
    for row in storage.acquisitions(provider_id="paris"):
        loan_id = (row.get("metadata") or {}).get("provider_loan_id")
        if loan_id:
            add(row, loan_id, source="acquisition")

    profile_cache: dict[str, str | None] = {}

    def profile(identity: str) -> str | None:
        if identity not in profile_cache:
            profile_cache[identity] = storage.adept_profile_binding_get("identity", identity) if identity else None
        return profile_cache[identity]

    groups: list[dict[str, Any]] = []
    for loan_id, entries in sorted(exposures.items()):
        exposures_for_loan = []
        for key, entry in sorted(entries.items(), key=lambda x: tuple(str(v) for v in x[0])):
            row = dict(entry)
            credential = row["credential_id"]
            account = row["account_id"]
            connection_identity = owners.get(credential)
            row["connection_consistent"] = bool(
                connection_identity and connection_identity == row["connection_identity_id"]
            )
            row["borrower_identity_id"] = None
            row["adept_profile_id"] = None
            if account is None:
                row["binding_state"] = "unknown_borrower"
            elif account in {"", "primary"}:
                row["borrower_identity_id"] = connection_identity
                row["adept_profile_id"] = profile(connection_identity or "")
                row["binding_state"] = (
                    "primary_profile_configured"
                    if row["connection_consistent"] and row["adept_profile_id"]
                    else "primary_unproven"
                )
            else:
                grant = grants.get((credential, account))
                if grant is None:
                    row["binding_state"] = "binding_required"
                else:
                    borrower_identity = str(grant.get("borrower_identity_id") or "")
                    pinned = str(grant.get("profile_id") or "")
                    row["borrower_identity_id"] = borrower_identity or None
                    row["adept_profile_id"] = pinned or None
                    if (not row["connection_consistent"] or
                            grant.get("connection_identity_id") != connection_identity):
                        row["binding_state"] = "stale_connection"
                    elif not pinned or profile(borrower_identity) != pinned or grant.get("confirmed_by") != "explicit":
                        row["binding_state"] = "stale_profile"
                    else:
                        row["binding_state"] = "locally_consistent"
            row["sources"].sort()
            exposures_for_loan.append(row)

        if credential_id is not None and not any(
            row["credential_id"] == credential_id for row in exposures_for_loan
        ):
            continue

        credentials = {row["credential_id"] for row in exposures_for_loan}
        profiles = {
            row["adept_profile_id"] for row in exposures_for_loan
            if row["adept_profile_id"] and row["binding_state"] in {
                "locally_consistent", "primary_profile_configured"
            }
        }
        shared = len(credentials) > 1
        inconsistent = any(
            row["binding_state"] not in {"locally_consistent", "primary_profile_configured"}
            for row in exposures_for_loan
        )
        recorded = {
            row["recorded_adept_profile_id"] for row in exposures_for_loan
            if row.get("recorded_adept_profile_id")
        }
        profiles_in_conflict = len(profiles | recorded) > 1
        groups.append({
            "loan_id": loan_id,
            "multiple_connections": shared,
            "profile_disagreement": profiles_in_conflict,
            "requires_attention": shared or inconsistent or profiles_in_conflict,
            "multiple_item_ids": len({row["item_id"] for row in exposures_for_loan}) > 1,
            "exposures": exposures_for_loan,
        })

    return {
        "provider_id": "paris",
        "credential_filter": credential_id,
        "summary": {
            "loan_groups": len(groups),
            "multiple_connection_groups": sum(x["multiple_connections"] for x in groups),
            "profile_disagreements": sum(x["profile_disagreement"] for x in groups),
            "attention_groups": sum(x["requires_attention"] for x in groups),
            "rows_without_loan_id": rows_without_loan_id,
        },
        "loans": groups,
        "note": (
            "Inventaire local uniquement : une liaison cohérente ne prouve pas "
            "l'autorisation Adobe. Plusieurs connexions exposant un même ID ne "
            "prouvent pas à elles seules une seule transaction. Aucun ADEPT ni "
            "POST Paris n'a été effectué."
        ),
    }
