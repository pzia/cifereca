"""#4662/#4988: explicit independent local send without a supplier loan.

A present placement owned by A suffices for a *request* to use the existing
native "Envoyer à" executor to make an independent copy in B. Provider loans,
ADEPT rights, and source credentials are never granted to B.
"""
from __future__ import annotations

from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from .library_mutation import _materialize_to_identity_locked


def send_present_placement_copy(
    service, *, source_placement_id: str, source_identity_id: str,
    source_artifact_id: str, requesting_identity_id: str,
    expected_account_context_epoch: int,
    expected_source_context_epoch: int, confirmed: bool = False,
) -> dict[str, object]:
    """Copy exactly one locally present placement after explicit consent.

    A GTK preview and source/identity parameters are not a licence to copy
    arbitrary bytes. The source placement, physical bytes, target and
    provenance are checked again by the existing canonical local-send
    executor, under its same cross-process acquisition lock.
    """
    blocked = {
        "status": "blocked", "loan": "not_attempted",
        "delivery": "unavailable", "transfer": "blocked",
        "reason_code": "local_send_scope_unverified",
        "retry_borrow": False,
    }
    if confirmed is not True:
        return {**blocked, "reason_code": "local_send_confirmation_required"}
    if (any(type(value) is not str or not 0 < len(value) <= 256
            or not value.isprintable() or value != value.strip()
            for value in (
                source_placement_id, source_identity_id,
                source_artifact_id, requesting_identity_id,
            ))
            or source_identity_id == requesting_identity_id
            or type(expected_account_context_epoch) is not int
            or expected_account_context_epoch < 0
            or type(expected_source_context_epoch) is not int
            or expected_source_context_epoch < 0):
        return blocked

    try:
        identity = service.current_identity
        if (getattr(identity, "identity_id", None) != requesting_identity_id
                or service._account_context_epoch
                   != expected_account_context_epoch
                or service._source_context_epoch
                   != expected_source_context_epoch):
            return {**blocked, "reason_code": "local_send_context_revoked"}

        with InterProcessLock(acquisition_lock_file()) as shared_lock:
            if (service.current_identity is not identity
                    or service._account_context_epoch
                       != expected_account_context_epoch
                    or service._source_context_epoch
                       != expected_source_context_epoch
                    or service.identity_by_id(source_identity_id) is None
                    or service.identity_by_id(requesting_identity_id) is not identity):
                return {**blocked, "reason_code": "local_send_context_revoked"}

            source = service.storage.placement_get(source_placement_id)
            if (type(source) is not dict
                    or source.get("identity_id") != source_identity_id
                    or source.get("artifact_id") != source_artifact_id
                    or source.get("state") != "present"):
                return {**blocked, "reason_code": "local_send_source_revoked"}

            # This *native* executor enforces physical-owner containment,
            # source inode/SHA, licensing-independent copy provenance,
            # recipient directory safety, and crash-safe canonical receipts.
            result = _materialize_to_identity_locked(
                service, source_placement_id, requesting_identity_id,
                shared_lock,
            )
            if (type(result) is not dict
                    or result.get("status") not in ("complete", "unchanged")
                    or result.get("target_identity_id") != requesting_identity_id
                    or type(result.get("placement_id")) is not str
                    or not result["placement_id"]):
                return {**blocked, "reason_code": "local_send_result_unverified"}
            recipient = service.storage.placement_get(result["placement_id"])
            if (type(recipient) is not dict
                    or recipient.get("identity_id") != requesting_identity_id
                    or recipient.get("state") != "present"):
                return {**blocked, "reason_code": "local_send_result_unverified"}

            # No file paths or provider/account identifiers leave the Core
            # boundary. A transfer does not assert or retry any supplier loan.
            if (service.current_identity is not identity
                    or service._account_context_epoch
                       != expected_account_context_epoch
                    or service._source_context_epoch
                       != expected_source_context_epoch):
                return {
                    **blocked, "delivery": "present",
                    "reason_code": "local_send_context_revoked",
                }
            return {
                "status": "complete", "loan": "not_attempted",
                "delivery": "present", "transfer": "completed",
                "copy": ("already_present" if result["status"] == "unchanged"
                         else "created"),
                "reason_code": "independent_local_copy_completed",
                "retry_borrow": False,
            }
    except Exception:
        # No provider retry, no raw errors, receipt paths or private data.
        return {**blocked, "reason_code": "local_send_result_unverified"}


__all__ = ["send_present_placement_copy"]
