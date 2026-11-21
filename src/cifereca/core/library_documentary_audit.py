"""#3995: scoped, aggregate-only documentary audit using Core's existing Library.

N1 persisted evidence is independent from optional bounded observations of
an exact registered N2 decision and its N3 bytes. No extraction, refresh or
provider dispatch is invoked.
The usual no-scan Library projection still performs its own Placement checks.
"""
from __future__ import annotations

from .covers import resolve_cover
from .local_cover_evidence import VerifiedLocalCoverEvidence
from .notice_c_local_origin_projection import (
    project_local_origin_public_c, valid_local_public_c_link,
)
from .notice_c_cover_index import read_consolidated_notice_cover
from ..plugin_api import normalize_language_tag

_KEYS = {
    "covers": ("embedded", "origin", "catalog", "ambiguous", "missing", "unknown"),
    "languages": ("canonical", "noncanonical", "invalid", "unknown"),
    "metadata": ("divergent", "complement", "observed", "unknown"),
}
_MAX_ROWS = 10000
# One selected EPUB probe can read up to 8 MiB. Limit physical IO per audit.
_MAX_RUNTIME_PROBES = 128
_N2_KEYS = ("embedded", "origin", "catalog", "pending", "ambiguous", "missing", "unknown")
_N3_KEYS = ("digest_valid", "signature_valid", "not_validated", "not_applicable", "unknown")


def _cover_state_and_packet(storage, row, owner):
    local = row.get("_exact_local_cover_evidence")
    if (type(local) is VerifiedLocalCoverEvidence
            and local.scope.identity_id == owner
            and local.scope.artifact_id == row.get("artifact_id")
            and local.scope.placement_id == row.get("placement_id")
            and local.scope.source_key == row.get("key")):
        candidates = local.candidates
        if local.embedded is not None:
            candidates += (local.embedded.candidate,)
        decision = resolve_cover(candidates, attested_book_identity=local.scope.source_key)
        if decision.status == "selected":
            return decision.tier, local
        if decision.status == "ambiguous":
            return "ambiguous", local
        return "unknown", local
    if local is not None:
        return "unknown", None
    link = row.get("_public_canonical_cover_link")
    if not valid_local_public_c_link(
        link, identity_id=owner, artifact_id=row.get("artifact_id"),
        placement_id=row.get("placement_id"),
    ):
        return "unknown", None
    c = read_consolidated_notice_cover(storage, link.document)
    if c is None or c.epoch != link.epoch:
        return "unknown", None
    if tuple(source.source_ref for source in c.sources) != link.source_refs:
        return "unknown", None
    # Partial inventory may still contain candidates but cannot certify N1.
    if not c.snapshot_complete:
        return "unknown", link
    decision = resolve_cover(c.candidates, attested_book_identity=c.attested_book_identity)
    if decision.status == "selected":
        return decision.tier, link
    if decision.status == "ambiguous":
        return "ambiguous", link
    if c.sources and all(source.state == "missing" for source in c.sources):
        return "missing", link
    return "unknown", link


def _runtime_states(observer, packet):
    """Validate internal observations without inferring either N2 or N3."""
    result = observer(packet)
    if type(result) is not tuple or len(result) != 3:
        return "unknown", "unknown"
    status, tier, physical = result
    if any(type(value) is not str for value in result):
        return "unknown", "unknown"
    if status == "selected" and tier in ("embedded", "origin", "catalog"):
        admissible = ("unknown", "not_validated",
                      "digest_valid" if tier == "embedded" else "signature_valid")
        return tier, physical if physical in admissible else "unknown"
    if status == "missing" and tier == "" and physical == "not_applicable":
        return "missing", "not_applicable"
    if status in ("pending", "ambiguous") and tier == "" and physical == "unknown":
        return status, "unknown"
    return "unknown", "unknown"


def _language_state(row):
    # No inference or attribution from a valid BCP47 tag. An absent or
    # partially unavailable source language remains an explicit unknown.
    meta = row.get("metadata")
    if type(meta) is not dict or len(meta) > 1024:
        return "unknown"
    tags = []
    for container in (row, meta):
        for key in ("language", "languages", "lang", "langs"):
            value = container.get(key)
            if value is None:
                continue
            if type(value) is str:
                value = (value,)
            if type(value) not in (list, tuple) or len(value) > 64:
                return "invalid"
            for tag in value:
                if type(tag) is not str or len(tag) > 128:
                    return "invalid"
                if tag.strip():
                    tags.append(tag.strip())
    if not tags:
        return "unknown"
    canonical = [normalize_language_tag(tag) for tag in tags]
    if any(type(tag) is not str for tag in canonical):
        return "invalid"
    return "canonical" if tags == canonical else "noncanonical"


_LANGUAGE_PROVENANCE = ("concordant", "divergent", "partial", "unknown")


def _classify_language_sources(values):
    # Compare decoded exact-source declarations as sets, never choose a majority.
    sets = {frozenset(value) for value in values if value}
    if len(sets) > 1:
        return "divergent"
    if not sets:
        return "unknown"
    # One explicit declaration is evidence, not corroboration. Only two
    # or more complete exact-source declarations can agree with each other.
    return "partial" if len(values) < 2 or any(not value for value in values) else "concordant"


def _retained_document(storage, row, owner):
    link = row.get("_public_canonical_document_link")
    if not valid_local_public_c_link(
        link, identity_id=owner, artifact_id=row.get("artifact_id"),
        placement_id=row.get("placement_id"),
    ):
        return None
    retained = read_consolidated_notice_cover(storage, link.document)
    if (retained is None or retained.epoch != link.epoch
            or tuple(source.source_ref for source in retained.sources) != link.source_refs):
        return None
    return retained


def _language_provenance(storage, row, owner):
    retained = _retained_document(storage, row, owner)
    if retained is None:
        return "unknown"
    values = []
    for source in retained.sources:
        # The retained decoder validates the exact-source declaration.
        bio = source.public_bibliography if source.visibility == "public" else None
        observation = getattr(bio, "language_observation", None)
        values.append(None if observation is None else observation.languages)
    # Cover completeness is not evidence of language completeness.
    return _classify_language_sources(values)


_METADATA_FIELDS = ("title", "author", "publisher", "published_at")


def _metadata_provenance(storage, row, owner):
    """Literal descriptive observations, never a selected canonical value."""
    retained = _retained_document(storage, row, owner)
    if retained is None:
        return dict.fromkeys(_METADATA_FIELDS, "unknown")
    result = {}
    for field in _METADATA_FIELDS:
        values, missing = set(), False
        for member in retained.sources:
            bio = member.public_bibliography if member.visibility == "public" else None
            seen = set()
            if bio is not None:
                primary = getattr(bio, field)
                if type(primary) is str and primary.strip():
                    seen.add(primary.strip())
                for detail in member.public_detail_facts:
                    if detail.field == field and type(detail.value) is str and detail.value.strip():
                        seen.add(detail.value.strip())
            values.update(seen)
            missing = missing or not seen
        result[field] = ("divergent" if len(values) > 1 else
                         "complement" if values and missing else
                         "observed" if values else "unknown")
    return result


def library_documentary_audit(service, *, identity_id, kind, runtime=False,
                              language_provenance=False):
    if type(kind) is not str or kind not in _KEYS:
        raise ValueError("invalid_audit_kind")
    keys = _KEYS[kind]
    if (runtime and kind != "covers"
            or language_provenance and kind != "languages"):
        raise ValueError("invalid_runtime_audit_kind")

    def report(status, entries, counts, n2=None, n3=None, provenance=None):
        result = {"schema": 2 if runtime or language_provenance else 1, "status": status,
                  "entries": entries, **counts}
        if runtime:
            result.update(n2=n2 if n2 is not None else dict.fromkeys(_N2_KEYS, 0),
                          n3=n3 if n3 is not None else dict.fromkeys(_N3_KEYS, 0))
        if kind == "metadata":
            result["fields"] = {field: dict.fromkeys(keys, 0) for field in _METADATA_FIELDS}
        if language_provenance:
            result["provenance"] = (provenance if provenance is not None
                                    else dict.fromkeys(_LANGUAGE_PROVENANCE, 0))
        return result

    def empty():
        return report("unavailable", 0, dict.fromkeys(keys, 0))
    if (type(identity_id) is not str or not 0 < len(identity_id) <= 256
            or identity_id != identity_id.strip() or not identity_id.isprintable()):
        return empty()
    try:
        if service.identity_by_id(identity_id) is None:
            return empty()
        generation = (service._account_context_epoch, service._source_context_epoch)
        rows = service.library_entries(identity_id=identity_id, scan=False)
        if (type(rows) is not list or len(rows) > _MAX_ROWS
                or any(type(row) is not dict or row.get("identity_id") != identity_id
                       for row in rows)):
            return empty()
        if language_provenance or kind == "metadata":
            # Reattest the documentary relation even when EPUB N1 wins.
            # Never mutate the Library snapshot or trust a supplied receipt.
            rows = [dict(row) for row in rows]
            project_local_origin_public_c(
                service.storage, rows, identity_id=identity_id, documentary_only=True,
            )
        counts = dict.fromkeys(keys, 0)
        n2, n3 = dict.fromkeys(_N2_KEYS, 0), dict.fromkeys(_N3_KEYS, 0)
        observer = getattr(service, "_documentary_cover_observer", None) if runtime else None
        provenance = dict.fromkeys(_LANGUAGE_PROVENANCE, 0)
        probes = 0
        fields = {field: dict.fromkeys(keys, 0) for field in _METADATA_FIELDS} if kind == "metadata" else None
        for row in rows:
            packet = None
            try:
                if kind == "covers":
                    status, packet = _cover_state_and_packet(service.storage, row, identity_id)
                elif kind == "languages":
                    status = _language_state(row)
                else:
                    states = _metadata_provenance(service.storage, row, identity_id)
                    status = next(key for key in keys if key in states.values())
            except (ValueError, TypeError, OSError, RuntimeError, AttributeError):
                status = "unknown"
                if fields is not None:
                    states = dict.fromkeys(_METADATA_FIELDS, "unknown")
            if fields is not None:
                for field, state in states.items():
                    fields[field][state] += 1
            counts[status if status in counts else "unknown"] += 1
            if language_provenance:
                try:
                    proof = _language_provenance(service.storage, row, identity_id)
                except Exception:
                    proof = "unknown"
                provenance[proof] += 1
            if runtime:
                linked, media = "unknown", "unknown"
                if packet is not None and callable(observer) and probes < _MAX_RUNTIME_PROBES:
                    probes += 1
                    try:
                        linked, media = _runtime_states(observer, packet)
                    except Exception:
                        pass
                n2[linked] += 1
                n3[media] += 1
        if runtime and observer is not getattr(service, "_documentary_cover_observer", None):
            # A replaced/closed window is not the same observation authority.
            n2 = dict.fromkeys(_N2_KEYS, 0)
            n3 = dict.fromkeys(_N3_KEYS, 0)
            n2["unknown"] = n3["unknown"] = len(rows)
        if (generation != (service._account_context_epoch, service._source_context_epoch)
                or service.identity_by_id(identity_id) is None):
            return empty()
    except Exception:
        return empty()
    result = report("partial", len(rows), counts, n2, n3, provenance)
    if fields is not None:
        result["fields"] = fields
    return result
