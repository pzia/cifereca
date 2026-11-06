"""#4552: deterministic, offline parser-witness comparison.

An approved witness ties *one exact source input digest* to critical
structured fields observed from a specific parser version. The comparator
never reads provider data, stores response bodies, calls a parser or writes
Core state. It cannot convert a source change into a confirmed code bug.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .notice_source_observation import (
    NoticeSourceKey, NoticeSourceObservation, _valid_key, _id as _source_id, admit_source_notice, _MAX_BYTES as _MAX_SOURCE_JSON_BYTES,
)

_MAX_FIELDS = 32
_MAX_DEPTH = 8
_MAX_KEY = 96
_MAX_BYTES = 65536
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,95}\Z")
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_$:-]{0,95}\Z")
_TYPES = frozenset(("string", "integer", "number", "boolean", "null", "array", "object"))


@dataclass(frozen=True, slots=True)
class CriticalParseField:
    """A structured raw parser key path, never a text-search expression."""

    path: tuple[str, ...]
    expected_type: str
    required: bool = True
    # Exact stable value is optional: no automatic comparison of volatile
    # summaries, loan availability, prices, signed links or stock.
    expected_sha256: str | None = None


@dataclass(frozen=True, slots=True, repr=False)
class ParseWitness:
    source_scope_sha256: str
    approved_input_sha256: str
    parser_version: str
    approved: bool
    critical_fields: tuple[CriticalParseField, ...]


@dataclass(frozen=True, slots=True)
class ParseWitnessReview:
    status: str  # unchanged | parser_regression_confirmed | source_changed |
                 # parser_drift_suspected | unverified
    affected_fields: tuple[str, ...]  # opaque, non-PII field ordinal labels
    # Always read-only, never permission to replace a previously good notice.
    executable: bool = False


def _valid_baseline(witness: object) -> bool:
    if (type(witness) is not ParseWitness
            or type(witness.approved) is not bool
            or type(witness.source_scope_sha256) is not str
            or type(witness.approved_input_sha256) is not str
            or _SHA.fullmatch(witness.source_scope_sha256) is None
            or _SHA.fullmatch(witness.approved_input_sha256) is None
            or type(witness.parser_version) is not str
            or _VERSION.fullmatch(witness.parser_version) is None
            or type(witness.critical_fields) is not tuple
            or not 1 <= len(witness.critical_fields) <= _MAX_FIELDS):
        return False
    known: set[tuple[str, ...]] = set()
    for fact in witness.critical_fields:
        if (type(fact) is not CriticalParseField
                or type(fact.path) is not tuple
                or not 1 <= len(fact.path) <= _MAX_DEPTH
                or any(type(k) is not str or _NAME.fullmatch(k) is None
                       for k in fact.path)
                or fact.path in known
                or type(fact.expected_type) is not str
                or fact.expected_type not in _TYPES
                or type(fact.required) is not bool
                or (fact.expected_sha256 is not None and (
                    type(fact.expected_sha256) is not str
                    or _SHA.fullmatch(fact.expected_sha256) is None
                ))):
            return False
        known.add(fact.path)
    return True


def _kind(value: object) -> str:
    if value is None:
        return "null"
    if type(value) is str:
        return "string"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is list:
        return "array"
    if type(value) is dict:
        return "object"
    return "unsupported"


def field_digest(value: object) -> str:
    """Digest one JSON value for a deliberately approved stable field."""
    try:
        body = json.dumps(
            value, ensure_ascii=True, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid_parser_witness_field") from exc
    if len(body) > _MAX_BYTES or _kind(value) == "unsupported":
        raise ValueError("invalid_parser_witness_field")
    return sha256(body).hexdigest()


def review_parse_witness(
    baseline: ParseWitness, *,
    source_scope_sha256: str,
    input_sha256: str | None, parser_version: str,
    raw_fields: object, complete: bool,
) -> ParseWitnessReview:
    """Classify one already parsed response without network or values in logs.

    A confirmed regression requires: approved baseline, exact same source
    scope and raw response bytes, a *different* parser version, complete
    parsing, and a missing/invalid critical field. Changed input bytes can
    only be 'source_changed', even when the new page is empty.
    """
    if (not _valid_baseline(baseline)
            or type(source_scope_sha256) is not str
            or _SHA.fullmatch(source_scope_sha256) is None
            or input_sha256 is not None and (
                type(input_sha256) is not str
                or _SHA.fullmatch(input_sha256) is None
            )
            or type(parser_version) is not str
            or _VERSION.fullmatch(parser_version) is None
            or type(complete) is not bool
            or type(raw_fields) is not dict):
        raise ValueError("invalid_parser_witness_review")
    if (not baseline.approved
            or source_scope_sha256 != baseline.source_scope_sha256
            or input_sha256 is None or not complete):
        return ParseWitnessReview("unverified", ())
    # The response content changed: comparisons against the old baseline
    # would generate false parsing-regression diagnoses. Never call this
    # confirmation, even if all fields are missing.
    if input_sha256 != baseline.approved_input_sha256:
        return ParseWitnessReview("source_changed", ())
    misses = []
    for position, field in enumerate(baseline.critical_fields, 1):
        current = raw_fields
        present = True
        for key in field.path:
            if type(current) is not dict or key not in current:
                present = False
                break
            current = current[key]
        violation = (not present and field.required) or (
            present and (
                _kind(current) != field.expected_type
                or field.expected_sha256 is not None
                and field_digest(current) != field.expected_sha256
            )
        )
        if violation:
            misses.append("critical_field_" + str(position))
    if not misses:
        return ParseWitnessReview("unchanged", ())
    status = ("parser_regression_confirmed"
              if parser_version != baseline.parser_version
              else "parser_drift_suspected")
    return ParseWitnessReview(status, tuple(misses))


def admitted_source_scope_sha256(
    key: NoticeSourceKey, *, parser_id: str,
) -> str:
    """Opaque identity/credential/edition scope for a Core-admitted witness.

    Parser ID is part of the domain. Changing the parser VERSION does not
    change the scope, so regressions across versions remain comparable.
    """
    if not _valid_key(key) or not _source_id(parser_id):
        raise ValueError("invalid_admitted_source_scope")
    payload = (
        "cifereca.notice-c.parse-witness-scope.v1",
        key.provider_id, key.visibility, key.identity_id,
        key.credential_id, key.account_id, key.branch_id,
        key.record_id, key.edition_id, key.manifestation_role,
        parser_id,
    )
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return sha256(raw.encode("ascii")).hexdigest()


def review_admitted_source_parse_witness(
    baseline: ParseWitness, *, observation: NoticeSourceObservation,
    input_sha256: str | None,
) -> ParseWitnessReview:
    """Review one typed Core admission, not caller-provided loose coordinates.

    The observation's SHA and raw JSON are round-trip checked before use:
    manually altered or noncanonical DTOs cannot relabel a parser regression.
    The Core must still hash the *actual authorized response bytes* to supply
    input_sha256; a provider's string claim is never an authenticity proof.
    """
    if (type(observation) is not NoticeSourceObservation
            or type(observation.raw_json) is not str
            or len(observation.raw_json) > _MAX_SOURCE_JSON_BYTES):
        # A forged DTO must never allocate an unbounded JSON tree before
        # the Core's normal parsed-source size gate runs.
        raise ValueError("invalid_admitted_source_observation")
    try:
        fields = observation.fields_copy()
        replay = admit_source_notice(
            key=observation.key,
            parser_id=observation.parser_id,
            parser_version=observation.parser_version,
            observed_at=observation.observed_at,
            raw_fields=fields,
            status=observation.status,
        )
        if (replay.source_revision != observation.source_revision
                or replay.raw_json != observation.raw_json):
            raise ValueError("admitted_observation_changed")
        scope = admitted_source_scope_sha256(
            observation.key, parser_id=observation.parser_id,
        )
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid_admitted_source_observation") from exc
    return review_parse_witness(
        baseline,
        source_scope_sha256=scope,
        input_sha256=input_sha256,
        parser_version=observation.parser_version,
        raw_fields=fields,
        complete=observation.status == "complete",
    )


__all__ = [
    "CriticalParseField", "ParseWitness", "ParseWitnessReview",
    "field_digest", "review_parse_witness",
    "admitted_source_scope_sha256", "review_admitted_source_parse_witness",
]
