"""Read-only local evidence snapshot and stale-review detection for P1 #976.

A SHA-256 digest is an equality checkpoint over the LOCAL recorded evidence,
not a proof of Adobe identity, a signed attestation, or an authorization.
Mutations may happen immediately after this read; any future *commit* must
revalidate inside its own transaction and independently consult the exact
Adobe/Paris sources. No commit path exists in this module.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any
import json
import re


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _valid_scope(loan_id: Any, item_id: Any) -> bool:
    return all(
        type(value) is str and bool(value.strip())
        and value == value.strip()
        and "\x00" not in value and "\n" not in value and "\r" not in value
        for value in (loan_id, item_id)
    )


def snapshot_paris_adept_local_evidence(
    storage: Any, *, loan_id: str, item_id: str,
) -> dict[str, Any]:
    """Fingerprint local historical and v14 observations for an exact pair.

    Includes raw local receipt *values* in the private hash input, not output.
    Therefore replacing one nonempty Adobe receipt by another changes the
    checkpoint even if the existing canonical diagnostics still say 'paired'.
    This is a read-only stability signal; it is not a capability token.
    """
    def result(status: str, fingerprint: str | None = None, *,
               rights: int = 0, acquisitions: int = 0,
               canonical_rows: int = 0) -> dict[str, Any]:
        return {
            "status": status,
            "algorithm": "sha256:paris-adept-local-evidence-v1",
            "fingerprint": fingerprint,
            "historical_right_count": rights,
            "historical_acquisition_count": acquisitions,
            "canonical_evidence_row_count": canonical_rows,
            "adobe_profile_resolution": "unknown",
            "return_authorized": False,
            "commit_available": False,
            "requires_explicit_review": True,
        }

    if not _valid_scope(loan_id, item_id):
        return result("invalid_exact_scope")

    try:
        rights = [
            row for row in storage.entitlements(provider_id="paris")
            if (type(row.get("provider_id")) is str
                and row["provider_id"].casefold() == "paris"
                and type(row.get("loan_id")) is str and row["loan_id"] == loan_id
                and type(row.get("item_id")) is str and row["item_id"] == item_id)
        ]
        # Historical acquisitions with the exact item may claim this loan
        # through a legacy entitlement ID. Keep all those rows in the
        # checkpoint; absence of an explicit provider_loan_id is not a
        # reason to omit potentially conflicting evidence.
        acquisitions = [
            row for row in storage.acquisitions(provider_id="paris")
            if (type(row.get("provider_id")) is str
                and row["provider_id"].casefold() == "paris"
                and type(row.get("item_id")) is str and row["item_id"] == item_id)
        ]
        with storage._lock, storage._connect() as db:
            canonical = db.execute(
                "SELECT s.source_reference_id,s.identity_id,s.credential_id,"
                "s.account_id,s.branch_id,s.edition_id,s.right_id,"
                "s.reference_kind,s.state,s.synchronizable,s.evidence_kind,"
                "s.evidence_ref,s.metadata_json,s.observed_at,s.updated_at,"
                "o.origin_link_id,o.origin_kind,o.identity_id,o.artifact_id,"
                "o.evidence_ref,o.metadata_json,o.updated_at "
                "FROM source_references AS s "
                "LEFT JOIN origin_links AS o ON "
                "o.source_reference_id=s.source_reference_id "
                "AND o.origin_kind IN ('provider','adept') "
                "WHERE s.provider_id='paris' AND s.reference_kind='loan' "
                "AND s.right_id=? AND s.edition_id=? "
                "ORDER BY s.source_reference_id,o.origin_link_id",
                (loan_id, item_id),
            ).fetchall()
        # JSON canonicalization is deliberately strict: a nonserializable
        # legacy value must fail closed instead of being coerced with str()
        # into a misleadingly reproducible attestation.
        def sorted_records(rows: list[Any]) -> list[str]:
            return sorted(json.dumps(
                row, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), allow_nan=False,
            ) for row in rows)

        encoded = json.dumps(
            {
                "version": 1,
                "provider_id": "paris",
                "loan_id": loan_id,
                "item_id": item_id,
                # Persisted SQL order is not an authority. Sorting keeps a
                # snapshot stable if the same evidence is returned in a
                # different order; duplicates remain visible and hashed.
                "historical_rights": sorted_records(rights),
                "historical_acquisitions": sorted_records(acquisitions),
                "canonical_rows": [list(row) for row in canonical],
            },
            sort_keys=True, ensure_ascii=False, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return result("unserializable_local_evidence")
    except Exception:
        # A missing table, unavailable SQLite DB or other storage exception
        # must never yield a plausible stale-safe fingerprint.
        return result("local_evidence_unavailable")

    if not rights and not acquisitions and not canonical:
        return result("no_local_evidence")
    return result(
        "local_snapshot", sha256(encoded).hexdigest(),
        rights=len(rights), acquisitions=len(acquisitions),
        canonical_rows=len(canonical),
    )


def validate_paris_adept_local_snapshot(
    storage: Any, *, loan_id: str, item_id: str, expected_fingerprint: str,
) -> dict[str, Any]:
    """Reobserve a fingerprint, refusing changed evidence without any writes."""
    if (not _valid_scope(loan_id, item_id)
            or type(expected_fingerprint) is not str
            or not _SHA256.fullmatch(expected_fingerprint)):
        return {
            "status": "invalid_snapshot_request",
            "evidence_unchanged": False,
            "return_authorized": False,
            "commit_available": False,
            "adobe_profile_resolution": "unknown",
            "requires_explicit_review": True,
        }

    current = snapshot_paris_adept_local_evidence(
        storage, loan_id=loan_id, item_id=item_id,
    )
    if current["status"] != "local_snapshot":
        status = "local_evidence_unavailable"
        unchanged = False
    elif current["fingerprint"] != expected_fingerprint:
        status = "local_evidence_changed"
        unchanged = False
    else:
        status = "local_evidence_unchanged_remote_attestation_required"
        unchanged = True
    return {
        "status": status,
        "evidence_unchanged": unchanged,
        "current_fingerprint": (
            current["fingerprint"] if current["status"] == "local_snapshot"
            else None
        ),
        "return_authorized": False,
        "commit_available": False,
        "adobe_profile_resolution": "unknown",
        "requires_explicit_review": True,
        "note": (
            "Cette comparaison ne prouve pas l'activation Adobe ni la "
            "validité actuelle du prêt Paris ; toute écriture future "
            "nécessite une revalidation atomique et une attestation serveur."
        ),
    }
