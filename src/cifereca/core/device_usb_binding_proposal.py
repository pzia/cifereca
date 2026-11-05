"""P1 #2854: declarative profile-to-USB-reader binding proposal.

A typed, deterministic *proposal* for the future "Associer une liseuse"
dialog. This helper never reads /sys or /dev, probes a mountpoint, mutates
SQLite or enables a reader. A declared UUID is a user claim, NEVER evidence
of physical USB presence, file rights, format support or permission to copy.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping, Sequence


_MAX_READERS_PER_PROFILE = 16
_MAX_GLOBAL_BINDINGS = 4096
_ALLOWED_UUID = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)


@dataclass(frozen=True, slots=True)
class UsbBindingProposal:
    identity_id: str
    device_id: str
    label: str
    usb_fingerprint: str
    already_bound: bool
    enabled: bool = False
    physically_attested: bool = False
    executable: bool = False


def propose_usb_reader_binding(
    *, identity_id: str, label: str, usb_fingerprint: str,
    existing: Sequence[Mapping[str, object]],
) -> UsbBindingProposal:
    """Validate exact declared scope and generate a stable non-authorizing ID.

    Does not save the proposal. The explicit GTK confirmation must re-read
    binding state and call Storage.device_usb_binding_declare transactionally.
    The actual scanner separately attests /dev/disk/by-uuid, sysfs, mountinfo
    and the device's identity immediately before reading.
    """
    if (type(identity_id) is not str or not 1 <= len(identity_id) <= 128
            or identity_id != identity_id.strip()
            or not identity_id.isprintable()
            or type(label) is not str or not 1 <= len(label) <= 90
            or label != label.strip() or not label.isprintable()
            or type(usb_fingerprint) is not str
            or not 8 <= len(usb_fingerprint) <= 128
            or any(c not in _ALLOWED_UUID for c in usb_fingerprint)
            or type(existing) not in (list, tuple)
            or len(existing) > _MAX_GLOBAL_BINDINGS):
        raise ValueError("invalid USB reader binding request")
    # Stable but non-identifying in public UI; no account names or raw UUIDs
    # appear in the device ID or automatic activity labels.
    generated_id = "usb-" + sha256(
        (identity_id + "\0" + usb_fingerprint.casefold()).encode("utf-8")
    ).hexdigest()[:32]
    target = usb_fingerprint.casefold()
    same: Mapping[str, object] | None = None
    owner_count = 0
    for row in existing:
        if (type(row) is not dict
                or type(row.get("device_id")) is not str
                or not row["device_id"]
                or type(row.get("identity_id")) is not str
                or not row["identity_id"]):
            raise ValueError("invalid existing reader scope")
        device_id, owner = row["device_id"], row["identity_id"]
        meta = row.get("metadata")
        fingerprint = meta.get("usb_fingerprint") if type(meta) is dict else None
        if owner == identity_id and type(fingerprint) is str:
            owner_count += 1
        if device_id == generated_id and (owner != identity_id
                or fingerprint != usb_fingerprint):
            raise ValueError("USB device binding ID collision")
        if type(fingerprint) is not str:
            # An unrelated legacy/non-USB device never confers a volume ID.
            continue
        if fingerprint.casefold() == target:
            if owner != identity_id or same is not None:
                raise ValueError("USB volume is bound or ambiguous")
            # Reuse an exact previously-declared device, never silently
            # migrate its ID, owner, UUID, desired states or receipts.
            if fingerprint != usb_fingerprint:
                raise ValueError("USB volume UUID case changed")
            same = row
    if same is not None:
        return UsbBindingProposal(
            identity_id=identity_id, device_id=same["device_id"],
            label=label, usb_fingerprint=usb_fingerprint,
            already_bound=True, enabled=same.get("enabled") is True,
        )
    if owner_count >= _MAX_READERS_PER_PROFILE:
        raise ValueError("reader limit reached for identity")
    return UsbBindingProposal(
        identity_id=identity_id, device_id=generated_id,
        label=label, usb_fingerprint=usb_fingerprint,
        already_bound=False,
    )


__all__ = ["UsbBindingProposal", "propose_usb_reader_binding"]
