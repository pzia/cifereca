"""Persist only validated, data-only virtual provider inventories in Core SQLite.

The stored record is not code, activation state or credentials. Active parent
plugins still supply local ProviderPlugin factories when an offline snapshot is
restored; inactive packages cannot be made active by a saved inventory.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Any

from ..plugin_api.provider_collection import (
    ProviderCollectionSnapshot,
    ProviderCollectionValidationError,
    validate_provider_collection_snapshot,
)

CACHE_VERSION = 1
MAX_SAVED_SNAPSHOT_BYTES = 262144
_STORAGE_PREFIX = "core.provider_collection.snapshot.v1:"


class ProviderCollectionSnapshotStore:
    """Immutable snapshot checkpoint per installed parent, without TTL expiry."""

    def __init__(self, storage: Any):
        self._storage = storage

    @staticmethod
    def key(parent_id: str) -> str:
        return _STORAGE_PREFIX + str(parent_id)

    @staticmethod
    def _canonical(value: dict[str, Any]) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")

    def save(self, snapshot: ProviderCollectionSnapshot) -> None:
        # Never serialize arbitrary child dictionaries, unknown fields or
        # objects provided by the plugin. Only the canonical validated model.
        validated = validate_provider_collection_snapshot(
            snapshot, parent_plugin_id=snapshot.parent_plugin_id,
        )
        data = asdict(validated)
        digest = sha256(self._canonical(data)).hexdigest()
        record = {
            "format": CACHE_VERSION,
            "parent_plugin_id": validated.parent_plugin_id,
            "digest": digest,
            "snapshot": data,
        }
        serialized = self._canonical(record)
        if len(serialized) > MAX_SAVED_SNAPSHOT_BYTES:
            raise ProviderCollectionValidationError("saved snapshot exceeds storage bound")
        self._storage.metadata_set(
            self.key(validated.parent_plugin_id), serialized.decode("utf-8"),
        )

    def load(self, parent_id: str) -> ProviderCollectionSnapshot | None:
        raw = self._storage.metadata_get(self.key(parent_id))
        if raw is None:
            return None
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_SAVED_SNAPSHOT_BYTES:
            raise ProviderCollectionValidationError("saved snapshot exceeds storage bound")
        try:
            record = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ProviderCollectionValidationError("saved snapshot is invalid JSON") from exc
        if not isinstance(record, dict):
            raise ProviderCollectionValidationError("saved snapshot envelope must be an object")
        if type(record.get("format")) is not int or record["format"] != CACHE_VERSION:
            raise ProviderCollectionValidationError("unsupported snapshot cache format")
        if record.get("parent_plugin_id") != parent_id:
            raise ProviderCollectionValidationError("saved snapshot parent mismatch")
        data = record.get("snapshot")
        if not isinstance(data, dict):
            raise ProviderCollectionValidationError("saved snapshot payload must be an object")
        if not isinstance(record.get("digest"), str) or (
                sha256(self._canonical(data)).hexdigest() != record["digest"]):
            raise ProviderCollectionValidationError("saved snapshot checksum mismatch")
        return validate_provider_collection_snapshot(data, parent_plugin_id=parent_id)


__all__ = ["ProviderCollectionSnapshotStore", "MAX_SAVED_SNAPSHOT_BYTES"]
