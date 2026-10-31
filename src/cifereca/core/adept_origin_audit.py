"""Read-only historical Paris/ADEPT origin audit (P0 #976, draft #987).

This is evidence for an exceptional/manual resolver, NEVER an authorization to
fulfill or return an Adobe license. It does not select a profile, make network
calls, mutate bindings/ledger, or alter cross-identity fail-closed guards.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .adept_return_origin import inventory_attests_return_origin


def _opaque_ref(*parts: Any) -> str:
    raw = json.dumps([str(p or "") for p in parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _artifact_state(metadata: Mapping[str, Any], artifact_inspector: Callable[[Path], Any]) -> str:
    text = str(metadata.get("path") or "").strip()
    if not text:
        return "not_recorded"
    path = Path(text)
    try:
        if not path.is_file():
            return "missing"
        inspection = artifact_inspector(path)
        # A malformed or partially available inspector result cannot attest
        # an unprotected, usable artifact. Treat unknown as unknown rather
        # than relying on truthiness or a missing attribute.
        valid = inspection.valid
        protected = inspection.protected
        if type(valid) is not bool or type(protected) is not bool:
            return "inspection_unavailable"
        if not valid:
            return "invalid"
        if protected:
            return "protected"
        return "usable"
    except Exception:
        # A third-party inspector may fail with a non-I/O exception; this
        # read-only audit must remain available without leaking its message.
        return "inspection_unavailable"


def inspect_paris_adept_origins(
    *,
    loan_id: str,
    item_id: str,
    entitlements: Iterable[Mapping[str, Any]],
    acquisitions: Iterable[Mapping[str, Any]],
    artifact_inspector: Callable[[Path], Any],
) -> dict[str, Any]:
    """Audit only an exact (provider, loan, item) pair, with no writes.

    A recorded ADEPT profile is a historical *claim*, not Adobe authorization.
    'inventory_attested' means existing persisted inventory evidence passes the
    established origin contract, not that the server was queried just now.
    Two source connections seeing one transaction are visibility, not proof of
    a double activation. A disagreement is never silently resolved here.
    """
    # Exact transaction keys are text, not stringifications of arbitrary
    # historical JSON. An object, bool or number cannot attest a Paris loan.
    provider_loan_id = loan_id.strip() if type(loan_id) is str else ""
    provider_item_id = item_id.strip() if type(item_id) is str else ""
    if (not provider_loan_id or not provider_item_id
            or "\x00" in provider_loan_id or "\x00" in provider_item_id):
        return {
            "status": "unknown", "shared_visibility": False,
            "profile_disagreement": False, "origin_resolution": "unknown",
            "adept_loan_reference_disagreement": False,
            "adobe_loan_state_disagreement": False,
            "sources": [], "origins": [],
        }

    rights = [
        row for row in entitlements
        if str(row.get("provider_id") or "").casefold() == "paris"
        and type(row.get("loan_id")) is str
        and row["loan_id"] == provider_loan_id
        and type(row.get("item_id")) is str
        and row["item_id"] == provider_item_id
    ]
    # Historical entitlement identifiers may collide across two Paris
    # connections or library identities. A global id -> row dict silently
    # overwrote one right and suppressed an otherwise exact legacy match.
    # Scope the old entitlement evidence to its recorded provenance.
    rights_by_source = {
        (
            str(row.get("entitlement_id")),
            str(row.get("identity_id") or ""),
            str(row.get("credential_id") or ""),
            str(row.get("account_id") or ""),
        )
        for row in rights if type(row.get("entitlement_id")) is str
        and row["entitlement_id"]
    }
    # Source visibility is supported only by persisted provider rights.
    # An ADEPT acquisition manually imported into another library identity
    # does NOT mean that its destination saw this loan at Paris.
    source_rows = [
        (
            str(row.get("identity_id") or ""),
            str(row.get("credential_id") or ""),
            str(row.get("account_id") or ""),
        )
        for row in rights
    ]
    recorded_source_keys = set(source_rows)

    origins = []
    for row in acquisitions:
        if (str(row.get("provider_id") or "").casefold() != "paris"
                or type(row.get("item_id")) is not str
                or row["item_id"] != provider_item_id
                or row.get("status") != "complete"
                or row.get("executor") != "kobo-adept"
                or row.get("strategy") != "loan_auto"):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        raw_loan_id = metadata.get("provider_loan_id")
        # Malformed metadata must not be coerced to an apparently exact
        # provider loan ID; nor may it fall back to a legacy entitlement.
        if raw_loan_id is not None and type(raw_loan_id) is not str:
            continue
        recorded_loan = (raw_loan_id or "").strip()
        if "\x00" in recorded_loan:
            continue
        if recorded_loan:
            if recorded_loan != provider_loan_id:
                continue
        else:
            # Legacy rows: only their exact entitlement can attest the
            # transaction, never title/ISBN/filename or the selected UI tab.
            source_key = (
                str(row.get("entitlement_id") or ""),
                str(row.get("identity_id") or ""),
                str(row.get("credential_id") or ""),
                str(row.get("account_id") or ""),
            )
            if (type(row.get("entitlement_id")) is not str
                    or source_key not in rights_by_source):
                continue

        identity = str(row.get("identity_id") or "")
        credential = str(row.get("credential_id") or "")
        account = str(row.get("account_id") or "")
        # This triple describes where acquisition was *recorded*, which
        # may be different from the original Paris borrower/source.
        # Never promote it to provider visibility without an entitlement.
        recorded_source = (identity, credential, account)
        binding = metadata.get("adept_binding")
        if not isinstance(binding, dict):
            binding = {}
        profile = binding.get("adept_profile_id")
        profile = profile.strip() if isinstance(profile, str) else ""
        attested = bool(profile and inventory_attests_return_origin(binding))
        strength = "inventory_attested" if attested else ("recorded" if profile else "unknown")
        origins.append({
            "library_identity_id": identity,
            "source_ref": _opaque_ref("paris", identity, credential, account),
            "source_visibility_evidence": (
                "persisted_entitlement" if recorded_source in recorded_source_keys
                else "acquisition_only"
            ),
            "acquisition_ref": _opaque_ref("acquisition", row.get("acquisition_id")),
            "recorded_adept_profile_id": profile or None,
            "recorded_adept_loan_ref": (
                _opaque_ref("adept-loan", binding["adept_loan_id"])
                if isinstance(binding.get("adept_loan_id"), str) and binding["adept_loan_id"].strip() else None
            ),
            "evidence": strength,
            # This is the last persisted Adobe inventory observation, NOT a
            # live server read, permission to return, or Paris loan status.
            # Unattested legacy flags cannot claim a completed Adobe return.
            "historic_adobe_loan_state": (
                binding.get("loan_state") if attested else "unknown"
            ),
            "artifact_state": _artifact_state(metadata, artifact_inspector),
        })
    sources = sorted({
        (identity, _opaque_ref("paris", identity, credential, account))
        for identity, credential, account in source_rows
    })
    origins.sort(key=lambda row: (row["source_ref"], row["acquisition_ref"]))
    profiles = {row["recorded_adept_profile_id"] for row in origins if row["recorded_adept_profile_id"]}
    strengths = {row["evidence"] for row in origins}
    # Both profile and historical ADEPT loan reference identify the recorded
    # fulfillment origin. An identical profile with distinct Adobe loan refs
    # is not a single safe return target. These refs are opaque diagnostics,
    # not a fresh server check and never an authorization to return.
    adept_loan_refs = {
        row["recorded_adept_loan_ref"]
        for row in origins if row["recorded_adept_loan_ref"]
    }
    loan_reference_disagreement = len(adept_loan_refs) > 1
    # Two persisted inventory snapshots may assert incompatible states for
    # the *same* Adobe loan reference. They are historical observations, not
    # a live ordering guarantee: never silently pick one as the return state.
    # Unattested flags are excluded because they are not inventory evidence.
    states_by_loan: dict[str, set[str]] = {}
    for origin in origins:
        ref = origin["recorded_adept_loan_ref"]
        if (ref and origin["evidence"] == "inventory_attested"
                and origin["historic_adobe_loan_state"] != "unknown"):
            states_by_loan.setdefault(ref, set()).add(origin["historic_adobe_loan_state"])
    state_disagreement = any(len(states) > 1 for states in states_by_loan.values())
    # The report-level status describes the strongest individual evidence,
    # not a consensus across transactions. In particular, one attested
    # profile does not override a different historical profile claim.
    # Keep the legacy status contract but expose resolution separately.
    if len(profiles) > 1 or loan_reference_disagreement or state_disagreement:
        origin_resolution = "conflicting_records"
    elif len(profiles) == 1 and any(
        not row["recorded_adept_profile_id"]
        or not row["recorded_adept_loan_ref"]
        for row in origins
    ):
        # A profile name alone cannot identify the exact Adobe transaction.
        # Even when another row has inventory evidence, a missing historical
        # ADEPT loan reference cannot be promoted to a single return origin.
        origin_resolution = "unknown"
    elif len(profiles) == 1 and all(
        row["evidence"] == "inventory_attested" for row in origins
    ):
        origin_resolution = "single_inventory_attested"
    elif len(profiles) == 1:
        origin_resolution = "single_recorded"
    else:
        origin_resolution = "unknown"
    status = (
        "inventory_attested" if "inventory_attested" in strengths else
        "recorded" if "recorded" in strengths else "unknown"
    )
    return {
        "status": status,
        # Distinct Paris credential/account scopes, not a count of local
        # library identities or acquisition destinations. Historical
        # entitlements do not attest present-day server visibility.
        "shared_visibility": len({
            (credential, account) for _, credential, account in source_rows
            if credential
        }) > 1,
        "profile_disagreement": len(profiles) > 1,
        "adept_loan_reference_disagreement": loan_reference_disagreement,
        "adobe_loan_state_disagreement": state_disagreement,
        "origin_resolution": origin_resolution,
        "sources": [
            {"library_identity_id": identity, "source_ref": ref}
            for identity, ref in sources
        ],
        "origins": origins,
    }
