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
    provider_loan_id = str(loan_id or "").strip()
    provider_item_id = str(item_id or "").strip()
    if not provider_loan_id or not provider_item_id:
        return {
            "status": "unknown", "shared_visibility": False,
            "profile_disagreement": False, "sources": [], "origins": [],
        }

    rights = [
        row for row in entitlements
        if str(row.get("provider_id") or "").casefold() == "paris"
        and str(row.get("loan_id") or "") == provider_loan_id
        and str(row.get("item_id") or "") == provider_item_id
    ]
    rights_by_id = {
        str(row.get("entitlement_id")): row for row in rights
        if row.get("entitlement_id")
    }
    source_rows = []
    for row in rights:
        source_rows.append((
            str(row.get("identity_id") or ""),
            str(row.get("credential_id") or ""),
            str(row.get("account_id") or ""),
        ))

    origins = []
    for row in acquisitions:
        if (str(row.get("provider_id") or "").casefold() != "paris"
                or str(row.get("item_id") or "") != provider_item_id
                or row.get("status") != "complete"
                or row.get("executor") != "kobo-adept"
                or row.get("strategy") != "loan_auto"):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        recorded_loan = str(metadata.get("provider_loan_id") or "")
        if recorded_loan:
            if recorded_loan != provider_loan_id:
                continue
        else:
            # Legacy rows: only their exact entitlement can attest the
            # transaction, never title/ISBN/filename or the selected UI tab.
            right = rights_by_id.get(str(row.get("entitlement_id") or ""))
            if right is None or any(
                str(row.get(k) or "") != str(right.get(k) or "")
                for k in ("identity_id", "credential_id", "account_id")
            ):
                continue

        identity = str(row.get("identity_id") or "")
        credential = str(row.get("credential_id") or "")
        account = str(row.get("account_id") or "")
        source_rows.append((identity, credential, account))
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
            "acquisition_ref": _opaque_ref("acquisition", row.get("acquisition_id")),
            "recorded_adept_profile_id": profile or None,
            "recorded_adept_loan_ref": (
                _opaque_ref("adept-loan", binding["adept_loan_id"])
                if isinstance(binding.get("adept_loan_id"), str) and binding["adept_loan_id"] else None
            ),
            "evidence": strength,
            "artifact_state": _artifact_state(metadata, artifact_inspector),
        })
    sources = sorted({
        (identity, _opaque_ref("paris", identity, credential, account))
        for identity, credential, account in source_rows
    })
    origins.sort(key=lambda row: (row["source_ref"], row["acquisition_ref"]))
    profiles = {row["recorded_adept_profile_id"] for row in origins if row["recorded_adept_profile_id"]}
    strengths = {row["evidence"] for row in origins}
    status = (
        "inventory_attested" if "inventory_attested" in strengths else
        "recorded" if "recorded" in strengths else "unknown"
    )
    return {
        "status": status,
        "shared_visibility": len({identity for identity, _ in sources if identity}) > 1,
        "profile_disagreement": len(profiles) > 1,
        "sources": [
            {"library_identity_id": identity, "source_ref": ref}
            for identity, ref in sources
        ],
        "origins": origins,
    }
