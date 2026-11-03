"""P1 #2854: immutable, identity-scoped reader Library list projection.

This is the read model for the future Liseuses MasterDetailPage. Consume only
previously loaded Library and SQLite snapshots; never scan USB, load covers,
fetch providers, acquire rights or mutate a device. Historical placement
observations cannot establish current physical presence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


_MAX_ROWS = 30000
_MAX_DEVICES = 100000
_FILTERS = frozenset({"all", "to_send", "to_remove", "without_intent"})


@dataclass(frozen=True, slots=True)
class ReaderLibraryBook:
    key: str
    artifact_id: str
    placement_id: str
    title: str
    author: str
    thumbnail_url: str | None
    desired_state: str
    current_presence: str
    last_known: str
    last_observed_at: int | None
    label: str


@dataclass(frozen=True, slots=True)
class ReaderLibrarySnapshot:
    identity_id: str
    device_id: str
    device_enabled: bool
    books: tuple[ReaderLibraryBook, ...]
    total_local: int
    wanted_keep: int
    wanted_remove: int
    no_intention: int
    # No USB scan is performed by this function, even if SQLite says present.
    current_presence_confirmed: bool = False


def _text(value: object, limit: int) -> str:
    if type(value) is not str:
        return ""
    value = value.strip()
    return value[:limit] if value.isprintable() else ""


def project_reader_library(
    *, identity_id: str,
    binding: Mapping[str, object],
    local_placements: Sequence[Mapping[str, object]],
    device_placements: Sequence[Mapping[str, object]],
    library_entries: Sequence[Mapping[str, object]],
    view_filter: str = "all",
) -> ReaderLibrarySnapshot:
    """A bound one-device snapshot, never permission to write or remove.

    Historical rows without exact `intent_identity_id` are refused
    *before* materializing any book title. The caller must select the
    identity and device explicitly and must not mix snapshots of profiles.
    Local book entries require both an exact identity-scoped Library entry
    and an exact local `state=present` placement.
    """
    if (type(identity_id) is not str or not identity_id
            or not isinstance(binding, Mapping)
            or binding.get("identity_id") != identity_id
            or type(binding.get("device_id")) is not str
            or not binding["device_id"]
            or type(view_filter) is not str or view_filter not in _FILTERS):
        raise ValueError("invalid reader identity, device or filter")
    metadata = binding.get("metadata")
    fp = metadata.get("usb_fingerprint") if type(metadata) is dict else None
    if type(fp) is not str or not 8 <= len(fp) <= 128:
        raise ValueError("reader is not a configured USB volume")
    if (type(local_placements) not in (list, tuple)
            or type(device_placements) not in (list, tuple)
            or type(library_entries) not in (list, tuple)
            or len(local_placements) > _MAX_ROWS
            or len(device_placements) > _MAX_DEVICES
            or len(library_entries) > _MAX_ROWS):
        raise ValueError("invalid or unbounded reader projection")
    device_id = binding["device_id"]
    verified_local: dict[str, str] = {}
    for row in local_placements:
        if (not isinstance(row, Mapping)
                or type(row.get("identity_id")) is not str
                or type(row.get("artifact_id")) is not str
                or type(row.get("placement_id")) is not str):
            raise ValueError("malformed local placement")
        if row["identity_id"] != identity_id:
            # A Library snapshot may include global rows. Never use their
            # file paths or rights to expose a book of another profile.
            continue
        if (row.get("state") == "present" and row["artifact_id"]
                and row["placement_id"]):
            verified_local.setdefault(row["artifact_id"], row["placement_id"])
    device_rows: dict[str, Mapping[str, object]] = {}
    for row in device_placements:
        if (not isinstance(row, Mapping)
                or row.get("device_id") != device_id
                or type(row.get("artifact_id")) is not str
                or not row["artifact_id"]
                or row["artifact_id"] in device_rows
                or type(row.get("metadata")) is not dict
                or row["metadata"].get("intent_identity_id") != identity_id):
            raise ValueError("foreign or ambiguous USB intention")
        desired = row.get("desired_state")
        if type(desired) is not str or desired not in ("keep", "remove"):
            raise ValueError("invalid USB intention")
        device_rows[row["artifact_id"]] = row
    books: list[ReaderLibraryBook] = []
    seen_keys: set[str] = set()
    seen_artifacts: set[str] = set()
    for entry in library_entries:
        if type(entry) is not dict:
            raise ValueError("malformed Library entry")
        if entry.get("identity_id") != identity_id:
            continue
        artifact_id = entry.get("artifact_id")
        if type(artifact_id) is not str or artifact_id not in verified_local:
            continue
        if artifact_id in seen_artifacts:
            # Multiple catalogue origins may share identical local bytes.
            # One device intention belongs to the *artifact*, not to a
            # guessed provider, title, edition or additional right.
            continue
        key = entry.get("key")
        if type(key) is not str or not key or key in seen_keys:
            raise ValueError("ambiguous Library row key")
        seen_keys.add(key)
        seen_artifacts.add(artifact_id)
        row = device_rows.get(artifact_id)
        desired = row["desired_state"] if row is not None else "none"
        last = row.get("observed_state") if row is not None else None
        last_known = last if type(last) is str and last in ("present", "missing") else "unknown"
        last_at = row.get("last_observed_at") if row is not None else None
        last_at = last_at if type(last_at) is int and last_at > 0 else None
        label = {
            "none": "Aucun souhait · présence non confirmée",
            "keep": "À mettre · présence à vérifier",
            "remove": "À retirer · présence à vérifier",
        }[desired]
        if (view_filter == "to_send" and desired != "keep"
                or view_filter == "to_remove" and desired != "remove"
                or view_filter == "without_intent" and desired != "none"):
            continue
        thumb = entry.get("thumbnail_url")
        books.append(ReaderLibraryBook(
            key=key, artifact_id=artifact_id,
            placement_id=verified_local[artifact_id],
            title=_text(entry.get("title"), 512) or "Sans titre",
            author=_text(entry.get("author"), 256),
            thumbnail_url=thumb if type(thumb) is str and len(thumb) <= 2048 else None,
            desired_state=desired,
            current_presence="unknown",
            last_known=last_known, last_observed_at=last_at,
            label=label,
        ))
    # Totals refer to the same currently selected filter, not an unrelated
    # unscoped account or a provider's remote Library.
    return ReaderLibrarySnapshot(
        identity_id=identity_id,
        device_id=device_id,
        device_enabled=binding.get("enabled") is True,
        books=tuple(books),
        total_local=len(books),
        wanted_keep=sum(r.desired_state == "keep" for r in books),
        wanted_remove=sum(r.desired_state == "remove" for r in books),
        no_intention=sum(r.desired_state == "none" for r in books),
    )


__all__ = ["ReaderLibraryBook", "ReaderLibrarySnapshot", "project_reader_library"]
