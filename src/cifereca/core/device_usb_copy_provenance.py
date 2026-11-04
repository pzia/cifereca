"""P1 #2854: live, owner-scoped canonical provenance for USB copy admission.

Canonical provenance establishes who owns the *local copy*, not whether its
licence permits USB duplication. An independent trusted policy verifier must
positively attest that distinct right for the same exact source/volume.
No provenance-only/metadata-only access grants a copy or creates an executor
token. This function is advisory and always non-mutating.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable

from .device_usb_copy_admission import UsbCopyRightsEvidence
from .device_usb_preview import _sha


_SOURCE_SQL = """
SELECT s.source_reference_id
FROM source_references AS s
JOIN artifacts AS a ON a.artifact_id=?
WHERE s.source_reference_id=?
  AND s.identity_id=?
  AND s.provider_id=?
  AND s.account_id=?
  AND s.reference_kind=?
  AND s.state='active'
  AND a.sha256=?
  AND a.media_format=?
  AND EXISTS (
    SELECT 1 FROM origin_links AS o
    WHERE o.source_reference_id=s.source_reference_id
      AND o.origin_kind='provider'
      AND o.identity_id=s.identity_id
      AND o.artifact_id=a.artifact_id
      AND o.evidence_ref<>''
  )
  AND EXISTS (
    SELECT 1 FROM placements AS p
    WHERE p.artifact_id=a.artifact_id
      AND p.identity_id=s.identity_id
      AND p.state='present'
  )
LIMIT 2
"""


def resolve_live_usb_copy_right(
    storage: Any,
    witness: UsbCopyRightsEvidence,
    *,
    policy_verifier: Callable[[UsbCopyRightsEvidence], bool] | None,
    now: int,
) -> UsbCopyRightsEvidence | None:
    """Return exact live evidence only after canonical AND policy attestation.

    The policy verifier must be a *trusted Core* provider/source policy
    resolver. Merely passing a function that returns True, or using this
    helper as the only check, is NOT a permission for a transfer. The future
    USB executor owns caller authentication, source policy validity, the
    separate user confirmation, and another in-lock recheck.
    """
    if (type(witness) is not UsbCopyRightsEvidence
            or type(now) is not int or now <= 0
            or witness.personal_usb_copy_permitted is not True
            or type(witness.identity_id) is not str
            or not witness.identity_id
            or type(witness.device_id) is not str
            or not witness.device_id
            or type(witness.usb_fingerprint) is not str
            or not witness.usb_fingerprint
            or type(witness.artifact_id) is not str
            or not witness.artifact_id
            or not _sha(witness.sha256)
            or type(witness.source_reference_id) is not str
            or not witness.source_reference_id
            or type(witness.provider_id) is not str
            or not witness.provider_id
            or type(witness.account_id) is not str
            or witness.source_kind not in ("purchase", "loan")
            or type(witness.selected_format) is not str
            or not witness.selected_format
            or type(witness.policy_ref) is not str
            or not witness.policy_ref
            or type(witness.observed_at) is not int
            or not 0 <= now - witness.observed_at <= 300
            or type(witness.valid_until) is not int
            or not now < witness.valid_until <= now + 300
            or (witness.source_kind == "loan" and (
                type(witness.loan_expires_at) is not int
                or witness.loan_expires_at <= now
            ))
            or not callable(policy_verifier)
            or not callable(getattr(storage, "_connect", None))
            or not hasattr(storage, "_lock")):
        return None
    params = (
        witness.artifact_id, witness.source_reference_id,
        witness.identity_id, witness.provider_id, witness.account_id,
        witness.source_kind, witness.sha256, witness.selected_format,
    )

    def matches() -> bool:
        with storage._lock, storage._connect() as db:
            return len(db.execute(_SOURCE_SQL, params).fetchall()) == 1

    try:
        if not matches():
            return None
        if policy_verifier(witness) is not True:
            return None
        # The independent provider policy check may have taken time.
        # Catch removal/refund, account reassignment or loss of the local
        # placement before returning an advisory positive result.
        if not matches():
            return None
    except (sqlite3.DatabaseError, TypeError, ValueError, OSError, RuntimeError):
        return None
    except Exception:
        # A provider callback may raise implementation-specific errors.
        return None
    return witness


__all__ = ["resolve_live_usb_copy_right"]
