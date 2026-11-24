"""Core-only durable selection fence for descriptive consolidators (#4552).

A trusted configuration owner supplies a vetted exact selection and executable
digest. No plugin loading, execution, provider IO or rights are granted here.
A revoked selection is a durable tombstone, never an implicit fallback.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from .notice_consolidator_selection import ExplicitSelection, _identifier

_PREFIX = "notice.c.descriptive-selection.v1:"


@dataclass(frozen=True, slots=True)
class DescriptiveSelectionPin:
    generation: int
    selection: ExplicitSelection | None
    installed_source_sha256: str | None


def _key(canonical_type, scope):
    if not _identifier(canonical_type) or not _identifier(scope, max_len=160):
        raise ValueError("invalid_descriptive_selection_scope")
    raw = json.dumps((canonical_type, scope), separators=(",", ":")).encode("ascii")
    return _PREFIX + sha256(raw).hexdigest()


def _validate(canonical_type, scope, selection, digest):
    if selection is None and digest is None:
        return
    if (type(selection) is not ExplicitSelection
            or selection.canonical_type != canonical_type or selection.scope != scope
            or not all(_identifier(value) for value in (
                selection.plugin_id, selection.version, selection.schema_id))
            or type(digest) is not str or len(digest) != 64
            or any(ch not in "0123456789abcdef" for ch in digest)):
        raise ValueError("invalid_descriptive_selection_pin")


def read_descriptive_selection_txn(db, *, canonical_type, scope):
    """Read the authoritative pin within an existing Core SQL snapshot."""
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    key = _key(canonical_type, scope)
    found = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    if found is None:
        return None
    try:
        raw = found[0]
        if type(raw) is not str or len(raw) > 4096:
            raise ValueError
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate_selection_field")
                result[key] = value
            return result

        record = json.loads(raw, object_pairs_hook=unique)
        if (type(record) is not dict
                or set(record) != {"generation", "selection", "installed_source_sha256"}
                or type(record["generation"]) is not int
                or not 1 <= record["generation"] < 2**63):
            raise ValueError
        payload = record["selection"]
        selection = None if payload is None else ExplicitSelection(**payload)
        digest = record["installed_source_sha256"]
        _validate(canonical_type, scope, selection, digest)
        return DescriptiveSelectionPin(record["generation"], selection, digest)
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ValueError("corrupt_descriptive_selection_pin") from exc


def set_descriptive_selection(storage, *, canonical_type, scope, selection,
                              installed_source_sha256, expected_generation):
    """CAS update by the trusted Core configuration owner, not a plugin.

    The caller must vet the installed registry and schema first. Revocation
    uses (None, None). An unchanged pin is idempotent; a changed pin advances
    its generation, including A -> B -> A and revocation/reselection.
    """
    key = _key(canonical_type, scope)
    _validate(canonical_type, scope, selection, installed_source_sha256)
    if type(expected_generation) is not int or not 0 <= expected_generation < 2**63:
        raise ValueError("invalid_descriptive_selection_generation")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = read_descriptive_selection_txn(db, canonical_type=canonical_type, scope=scope)
        generation = 0 if prior is None else prior.generation
        if generation != expected_generation:
            raise ValueError("descriptive_selection_generation_changed")
        if prior is not None and (prior.selection, prior.installed_source_sha256) == (
                selection, installed_source_sha256):
            return prior
        if generation + 1 >= 2**63:
            raise ValueError("invalid_descriptive_selection_generation")
        pin = DescriptiveSelectionPin(generation + 1, selection, installed_source_sha256)
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(asdict(pin), sort_keys=True, separators=(",", ":"))),
        )
        return pin


def require_descriptive_selection(pin, selection, digest):
    """An existing pin must exactly authorize the captured execution."""
    if pin is not None and (pin.selection != selection
                            or pin.installed_source_sha256 != digest):
        raise ValueError("descriptive_selection_changed")
