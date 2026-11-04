"""P1 #2854: reserve a deterministic managed USB target, without any USB I/O.

A reservation is only a per-identity declarative destination for an existing
"keep" wish. It does NOT attest a file, a compatible reader, DRM status,
transfer rights, physical volume, successful copy or future execution.

The actual write transaction remains Storage.device_usb_target_set(), which
checks the exact scope, immutability and case-insensitive path collisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .device_usb_preview import _relative, _sha
from .device_usb_variant_review import _SUFFIX


_MAX_INTENTIONS = 1000
_ALLOWED_UUID = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)


@dataclass(frozen=True, slots=True)
class UsbTargetProposal:
    identity_id: str
    device_id: str
    artifact_id: str
    usb_fingerprint: str
    selected_format: str
    target_relative_path: str
    reason: str = "destination_only_rights_unverified"
    executable: bool = False


def propose_usb_target(
    *, identity_id: str, binding: Mapping[str, object],
    intention: Mapping[str, object], artifact: Mapping[str, object],
    selected_format: str, other_intentions: Sequence[Mapping[str, object]],
) -> UsbTargetProposal:
    """Propose a unique SHA-named path for the *exact* existing EPUB/PDF kind.

    No filename/title is taken from a provider, and no format is inferred
    from extension or MIME. A plain EPUB never becomes a KEPUB through a
    different filename. A caller must re-read all inputs before committing
    the returned string through Storage; the proposal is NOT an authority.
    """
    if (type(identity_id) is not str or not identity_id
            or not isinstance(binding, Mapping)
            or binding.get("identity_id") != identity_id
            or type(binding.get("device_id")) is not str
            or not binding["device_id"]):
        raise ValueError("usb_target_scope_unverified")
    device_id = binding["device_id"]
    meta = binding.get("metadata")
    fingerprint = meta.get("usb_fingerprint") if type(meta) is dict else None
    managed = binding.get("target_directory")
    if (type(fingerprint) is not str or not 8 <= len(fingerprint) <= 128
            or any(char not in _ALLOWED_UUID for char in fingerprint)
            or not _relative(managed)):
        raise ValueError("usb_target_scope_unverified")
    if (not isinstance(artifact, Mapping)
            or type(artifact.get("artifact_id")) is not str
            or not artifact["artifact_id"]
            or type(artifact.get("sha256")) is not str
            or not _sha(artifact["sha256"])
            or type(artifact.get("size")) is not int
            or artifact["size"] <= 0 or artifact.get("kind") != "file"):
        raise ValueError("usb_target_artifact_unverified")
    artifact_id = artifact["artifact_id"]
    digest = artifact["sha256"]
    if artifact_id.startswith("sha256:") and artifact_id != "sha256:" + digest:
        raise ValueError("usb_target_artifact_unverified")
    if (type(selected_format) is not str or selected_format not in _SUFFIX
            or type(artifact.get("media_format")) is not str
            or artifact["media_format"] != selected_format):
        raise ValueError("usb_target_variant_unverified")
    # Known encrypted content is not suitable even for a suggested export.
    # Conversely, missing/false markers NEVER establish unprotected content.
    metadata = artifact.get("metadata")
    if type(metadata) is dict and (
            metadata.get("protected") is True
            or metadata.get("artifact_protected") is True
            or metadata.get("drm_status") in ("encrypted", "protected")
            or metadata.get("drm_scheme") in ("adept", "lcp", "kdrm")):
        raise ValueError("usb_target_protection_reported")
    if (not isinstance(intention, Mapping)
            or intention.get("device_id") != device_id
            or intention.get("artifact_id") != artifact_id
            or intention.get("desired_state") != "keep"
            or intention.get("observed_state") != "unknown"
            or intention.get("last_observed_at") is not None
            or type(intention.get("metadata")) is not dict
            or intention["metadata"] != {"intent_identity_id": identity_id}):
        raise ValueError("usb_target_intention_unverified")
    path = managed + "/" + digest + _SUFFIX[selected_format]
    if not _relative(path):
        raise ValueError("usb_target_path_unverified")
    if (intention.get("target_relative_path") is not None
            and intention.get("target_relative_path") != path):
        raise ValueError("usb_target_existing_destination_immutable")
    if (type(other_intentions) not in (tuple, list)
            or len(other_intentions) > _MAX_INTENTIONS):
        raise ValueError("usb_target_inventory_unverified")
    seen: set[str] = set()
    for other in other_intentions:
        if (type(other) is not dict
                or other.get("device_id") != device_id
                or type(other.get("artifact_id")) is not str
                or not other["artifact_id"]
                or other["artifact_id"] in seen
                or type(other.get("metadata")) is not dict
                or other["metadata"].get("intent_identity_id") != identity_id):
            raise ValueError("usb_target_inventory_unverified")
        seen.add(other["artifact_id"])
        target = other.get("target_relative_path")
        if target is not None and not _relative(target):
            raise ValueError("usb_target_inventory_unverified")
        if (other["artifact_id"] != artifact_id and type(target) is str
                and target.casefold() == path.casefold()):
            raise ValueError("usb_target_destination_occupied")
    # A missing or invented current intention cannot be made valid just by a
    # separately supplied sibling list.
    if artifact_id not in seen:
        raise ValueError("usb_target_inventory_unverified")
    return UsbTargetProposal(
        identity_id=identity_id, device_id=device_id,
        artifact_id=artifact_id, usb_fingerprint=fingerprint,
        selected_format=selected_format, target_relative_path=path,
    )


__all__ = ["UsbTargetProposal", "propose_usb_target"]
