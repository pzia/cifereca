"""Q12: independently pinned archives, resolved only by the Core lifecycle owner.

No installation, automatic trust, runtime scheduling or plugin broker.
Construct from trusted deployment inputs, never package metadata or provider
data. A candidate is a snapshot; callers still serialize manager lifecycle
and fence publication against the current durable selection generation.
"""
from contextlib import contextmanager
from dataclasses import dataclass

from .notice_consolidator_archive import qualify_consolidator_archive
from .notice_consolidator_active_inventory import hold_active_consolidator_inventory
from .notice_consolidator_selection import select_consolidator
from .notice_descriptive_selection_store import (
    DescriptiveSelectionPin, require_descriptive_selection,
)

_MAX_ARCHIVES = 16
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_CANDIDATES = 128


@dataclass(frozen=True, slots=True, repr=False)
class _Entry:
    payload: bytes
    digest: str
    inventory: object


class TrustedConsolidatorRegistry:
    """Bounded in-memory trust inputs; not an installed registry or a cache."""

    def __init__(self, archives):
        if (type(archives) is not tuple or len(archives) > _MAX_ARCHIVES
                or any(type(pair) is not tuple or len(pair) != 2
                       or type(pair[0]) is not bytes or type(pair[1]) is not str
                       for pair in archives)
                or sum(len(pair[0]) for pair in archives) > _MAX_TOTAL_BYTES):
            raise ValueError("invalid_trusted_consolidator_archives")
        entries = []
        seen = set()
        coordinates = set()
        for payload, digest in archives:
            inventory = qualify_consolidator_archive(payload, expected_sha256=digest)
            package = (inventory.package_id, inventory.package_version)
            if package in seen:
                raise ValueError("ambiguous_trusted_consolidator_archive")
            seen.add(package)
            for candidate in inventory.candidates:
                d = candidate.declaration
                key = (d.canonical_type, d.scope, d.plugin_id, d.version)
                if key in coordinates:
                    raise ValueError("ambiguous_trusted_consolidator_archive")
                coordinates.add(key)
                if len(coordinates) > _MAX_CANDIDATES:
                    raise ValueError("invalid_trusted_consolidator_archives")
            entries.append(_Entry(payload, digest, inventory))
        self._entries = tuple(entries)

    def public_age_digests(self):
        """Only trusted declarations for the currently supported worker type."""
        return tuple(sorted({
            candidate.installed.source_sha256
            for entry in self._entries for candidate in entry.inventory.candidates
            if candidate.declaration.canonical_type == "age"
        }))

    @contextmanager
    def hold_selected(self, manager, pin):
        """Select under managed package lease; never trust a stale snapshot.

        Called by the serialized lifecycle owner. A revoked, legacy or
        absent pin or an untrusted/disabled owner yields no candidate.
        Exact code/schema drift is an explicit refusal, never fallback.
        """
        if pin is None:
            yield None
            return
        if type(pin) is not DescriptiveSelectionPin:
            raise ValueError("invalid_descriptive_selection_pin")
        selection = pin.selection
        if selection is None or pin.schema_sha256 is None:
            yield None
            return
        coordinate = (selection.canonical_type, selection.scope,
                      selection.plugin_id, selection.version)
        options = dict(canonical_type=coordinate[0], scope=coordinate[1],
                       selected_plugin_id=coordinate[2], selected_version=coordinate[3])
        for entry in self._entries:
            candidates = entry.inventory.candidates
            declared = select_consolidator(
                tuple(row.declaration for row in candidates), **options,
            )
            if declared is None:
                continue
            candidate, = (row for row in candidates
                          if select_consolidator((row.declaration,), **options) is not None)
            require_descriptive_selection(pin, declared, candidate.installed.source_sha256,
                                          schema=candidate.schema)
            with hold_active_consolidator_inventory(
                manager, entry.payload, expected_sha256=entry.digest,
                expected_selection=coordinate,
            ) as active:
                matched = tuple(row for row in active if row == candidate)
                if len(matched) > 1:
                    raise ValueError("ambiguous_trusted_consolidator_archive")
                yield matched[0] if matched else None
            return
        yield None
