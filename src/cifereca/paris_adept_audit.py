"""Read-only evidence report for Paris loans appearing under multiple connections.

This is an offline reconciliation aid, not an Adobe ownership verifier.
No provider session, fulfillment, retry or mutation is ever performed here.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .core.adept_origin_audit import _opaque_ref


# The audit is a copyable CLI report: ledger state is an enum, never free text.
# Unknown/historical values remain visible as `unknown`, without secrets.
_AUDIT_STATUSES = frozenset({
    "complete", "failed", "attention_required", "cancelled", "aborted",
    "pending", "partial", "skipped", "running", "unknown",
    "reconcile_required", "unchanged",
})


def build_paris_adept_audit(storage: Any, *, credential_id: str | None = None) -> dict[str, Any]:
    """Cross-reference active Paris entitlements, acquisitions and pinned grants."""
    # A duplicate credential bound to different identities cannot prove its
    # owner. The previous last-row-wins dict silently selected whichever
    # connection happened to be returned last by SQLite.
    owner_candidates: dict[str, set[str]] = defaultdict(set)
    for row in storage.provider_connections():
        if str(row.get("provider_id") or "").casefold() != "paris":
            continue
        credential = str(row.get("credential_id") or "").strip()
        identity = str(row.get("identity_id") or "").strip()
        if credential:
            # A credential with an unbound row is NOT a unique owner merely
            # because a second row happens to have a valid identity.
            owner_candidates[credential].add(identity)
    owners: dict[str, str | None] = {
        credential: next(iter(identities)) if len(identities) == 1 else None
        for credential, identities in owner_candidates.items()
    }
    # Duplicate grants must fail closed. A dict comprehension previously
    # accepted whichever historical row happened to be returned last.
    grants: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for grant in storage.paris_borrower_adept_bindings():
        if not isinstance(grant, dict):
            continue
        credential = str(grant.get("credential_id") or "").strip()
        account = str(grant.get("account_id") or "").strip()
        if credential and account:
            grants[(credential, account)].append(grant)
    exposures: dict[str, dict[tuple[str, str | None, str, str], dict[str, Any]]] = defaultdict(dict)
    rows_without_loan_id = 0

    def add(row: dict[str, Any], loan_id: str, *, source: str) -> None:
        nonlocal rows_without_loan_id
        loan_key = loan_id.strip() if type(loan_id) is str else ""
        if not loan_key or "\x00" in loan_key:
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
            statuses = current.setdefault("acquisition_statuses", [])
            raw_status = row.get("status")
            status = raw_status if type(raw_status) is str and raw_status in _AUDIT_STATUSES else "unknown"
            if status not in statuses:
                statuses.append(status)
            # A completed and failed ledger row with the same transaction
            # scope is contradictory; never silently choose the last one.
            current["acquisition_status"] = (
                status if len(statuses) == 1 else "ambiguous"
            )
            # A failure/attention receipt may record a *proposed* ADEPT
            # profile or token before the operation failed. It is diagnostic
            # status, not a successful historical fulfillment claim. Never
            # let it create or conflict with recorded origin evidence.
            # A receipt with an unknown status must remain explicitly
            # ambiguous even if another receipt for the same scope completed.
            if status != "complete":
                return
            # Non-Adobe deliveries cannot attest an Adobe fulfillment.
            # Absent executor/strategy fields are legacy recorded evidence only.
            if (row.get("executor") not in (None, "", "kobo-adept")
                    or row.get("strategy") not in (None, "", "loan_auto")):
                return
            metadata = row.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            ade = metadata.get("adept_binding")
            if isinstance(ade, dict):
                profile_id = ade.get("adept_profile_id")
                if isinstance(profile_id, str) and profile_id.strip():
                    profiles = current.setdefault("recorded_adept_profile_ids", [])
                    profile_id = profile_id.strip()
                    if profile_id not in profiles:
                        profiles.append(profile_id)
                    # A conflicting ADEPT profile has *no* single origin.
                    current["recorded_adept_profile_id"] = (
                        profile_id if len(profiles) == 1 else None
                    )
                # Adobe loan reference is distinct from both the Paris loan
                # and the locally configured ADEPT profile. Expose only an
                # opaque digest, never the private license/activation token.
                loan_ref = ade.get("adept_loan_id")
                if isinstance(loan_ref, str) and loan_ref.strip():
                    refs = current.setdefault("recorded_adept_loan_refs", [])
                    opaque = _opaque_ref("adept-loan", loan_ref.strip())
                    if opaque not in refs:
                        refs.append(opaque)
                    current["recorded_adept_loan_ref"] = (
                        opaque if len(refs) == 1 else None
                    )

    for row in storage.entitlements(provider_id="paris", state="active"):
        if str(row.get("provider_id") or "paris").casefold() == "paris":
            add(row, row.get("loan_id"), source="entitlement")
    for row in storage.acquisitions(provider_id="paris"):
        if str(row.get("provider_id") or "paris").casefold() != "paris":
            continue
        metadata = row.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        loan_id = metadata.get("provider_loan_id")
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
            elif account in {"", "primary"} and (credential, "primary") not in grants:
                row["borrower_identity_id"] = connection_identity
                row["adept_profile_id"] = profile(connection_identity or "")
                row["binding_state"] = (
                    "primary_profile_configured"
                    if row["connection_consistent"] and row["adept_profile_id"]
                    else "primary_unproven"
                )
            else:
                candidates = grants.get(
                    (credential, "primary" if account in {"", "primary"} else account), ()
                )
                if not candidates:
                    row["binding_state"] = "binding_required"
                elif len(candidates) != 1:
                    # No borrower/profile is inferred from inconsistent grants.
                    row["binding_state"] = "ambiguous_binding"
                else:
                    grant = candidates[0]
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
            if "recorded_adept_profile_ids" in row:
                row["recorded_adept_profile_ids"].sort()
            if "acquisition_statuses" in row:
                row["acquisition_statuses"].sort()
            if "recorded_adept_loan_refs" in row:
                row["recorded_adept_loan_refs"].sort()
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
            profile for row in exposures_for_loan
            for profile in row.get("recorded_adept_profile_ids", (
                [row["recorded_adept_profile_id"]]
                if row.get("recorded_adept_profile_id") else []
            ))
        }
        # A Paris loan ID may be reused for distinct books. Only compare
        # historical Adobe claims within the exact (loan, item) group.
        # Configured-vs-recorded is a clue, never proof of wrong activation.
        recorded_by_item: dict[str, set[str]] = defaultdict(set)
        loan_refs_by_item: dict[str, set[str]] = defaultdict(set)
        for row in exposures_for_loan:
            item = row["item_id"]
            recorded_by_item[item].update(row.get("recorded_adept_profile_ids", ()))
            loan_refs_by_item[item].update(row.get("recorded_adept_loan_refs", ()))
        recorded_profile_conflict = any(
            len(items) > 1 for items in recorded_by_item.values()
        )
        configured_profile_divergence = len(profiles) > 1
        configured_vs_recorded_difference = bool(
            profiles and recorded and profiles != recorded
        )
        profiles_in_conflict = len(profiles | recorded) > 1
        loan_refs = set().union(*loan_refs_by_item.values()) if loan_refs_by_item else set()
        loan_reference_disagreement = any(
            len(items) > 1 for items in loan_refs_by_item.values()
        )
        acquisition_rows = [
            row for row in exposures_for_loan if "acquisition" in row["sources"]
        ]
        multiple_items = len({row["item_id"] for row in exposures_for_loan}) > 1
        if multiple_items:
            # Equal Paris loan IDs with different items do not attest one
            # transaction; report their distinct records without collapsing.
            origin_resolution = "unknown"
        elif recorded_profile_conflict or loan_reference_disagreement:
            origin_resolution = "conflicting_records"
        elif (len(recorded) == len(loan_refs) == 1
              and acquisition_rows
              and all(row.get("recorded_adept_profile_id")
                      and row.get("recorded_adept_loan_ref")
                      for row in acquisition_rows)):
            # A recorded identity is evidence for review, NEVER authority to
            # return or fulfill: inventory confirmation is not performed here.
            origin_resolution = "single_recorded"
        else:
            origin_resolution = "unknown"
        ledger_status_ambiguous = any(
            len(row.get("acquisition_statuses", ())) > 1
            or "unknown" in row.get("acquisition_statuses", ())
            for row in exposures_for_loan
        )
        # Advisory-only, closed vocabulary for a dedicated human review.
        # These codes are never interpreted as permission to change bindings,
        # replay an ACSM, or return a license. One Paris loan ID alone cannot
        # prove that two entries represent the same transaction.
        reasons: list[str] = []
        if multiple_items:
            reasons.append("paris_loan_id_reused")
        if recorded_profile_conflict:
            reasons.append("recorded_adobe_profiles_conflict")
        if loan_reference_disagreement:
            reasons.append("recorded_adobe_loan_refs_conflict")
        if inconsistent:
            reasons.append("source_binding_unverified")
        if configured_profile_divergence:
            reasons.append("configured_profiles_diverge")
        if ledger_status_ambiguous:
            reasons.append("acquisition_receipts_conflict")
        if configured_vs_recorded_difference:
            reasons.append("configured_vs_recorded_profile_differs")
        if shared:
            reasons.append("multiple_paris_connections")
        has_recorded_claim = bool(recorded or loan_refs)
        if has_recorded_claim and origin_resolution == "unknown":
            reasons.append("historical_adobe_origin_incomplete")
        if (multiple_items or recorded_profile_conflict
                or loan_reference_disagreement or configured_profile_divergence):
            next_review = "compare_exact_loan_and_adobe_transactions"
        elif inconsistent:
            next_review = "inspect_paris_account_binding"
        elif ledger_status_ambiguous or (
            has_recorded_claim and origin_resolution == "unknown"
        ):
            next_review = "inspect_historical_adobe_inventory"
        elif configured_vs_recorded_difference:
            next_review = "compare_configured_vs_historical_profile"
        elif shared:
            next_review = "check_cross_connection_loan_visibility"
        else:
            next_review = "none"
        groups.append({
            "loan_id": loan_id,
            "multiple_connections": shared,
            "profile_disagreement": profiles_in_conflict,  # legacy aggregate
            "recorded_profile_conflict": recorded_profile_conflict,
            "configured_profile_divergence": configured_profile_divergence,
            "configured_vs_recorded_difference": configured_vs_recorded_difference,
            "adept_loan_reference_disagreement": loan_reference_disagreement,
            "origin_resolution": origin_resolution,
            "review_reasons": reasons,
            "next_read_only_review": next_review,
            "requires_attention": bool(reasons) or profiles_in_conflict,
            "ledger_status_ambiguous": ledger_status_ambiguous,
            "multiple_item_ids": multiple_items,
            "exposures": exposures_for_loan,
        })

    return {
        "provider_id": "paris",
        "credential_filter": credential_id,
        "summary": {
            "loan_groups": len(groups),
            "multiple_connection_groups": sum(x["multiple_connections"] for x in groups),
            "profile_disagreements": sum(x["profile_disagreement"] for x in groups),
            "recorded_profile_conflicts": sum(x["recorded_profile_conflict"] for x in groups),
            "adept_loan_reference_disagreements": sum(
                x["adept_loan_reference_disagreement"] for x in groups
            ),
            "attention_groups": sum(x["requires_attention"] for x in groups),
            "rows_without_loan_id": rows_without_loan_id,
        },
        "loans": groups,
        "note": (
            "Inventaire local uniquement : une liaison cohérente ne prouve pas "
            "l'autorisation Adobe. Plusieurs connexions exposant un même ID ne "
            "prouvent pas à elles seules une seule transaction. Aucun ADEPT ni "
            "POST Paris n'a été effectué. Un écart avec le profil actuellement "
            "configuré n'atteste pas une mauvaise activation ADEPT ; les "
            "références historiques ne valent jamais autorisation de retour."
        ),
    }
