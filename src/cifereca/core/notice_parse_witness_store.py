"""#4552 Q5/Q7: Core-owned approved parser-witness baselines, not a parser.

One exact source/parser scope owns one approved baseline in the existing
SQLite metadata table. Only an explicit Core admission path may call publish;
there is no periodic probe, provider HTTP, second Notice C writer or deletion.
No raw response, session, credential or parser payload is persisted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from .notice_source_observation import (
    NoticeSourceKey, NoticeSourceObservation, admit_source_notice,
)
from .notice_parse_witness import (
    CriticalParseField, ParseWitness, _valid_baseline,
    admitted_source_scope_sha256, review_admitted_source_parse_witness,
)

_PREFIX = "notice.c.parse-witness.v1:"
_MAX_RECORD_BYTES = 32768
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_GENERATION = 2**63 - 1


@dataclass(frozen=True, slots=True, repr=False)
class ApprovedParseWitness:
    generation: int
    baseline: ParseWitness


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("corrupt_parse_witness_baseline")
        result[key] = value
    return result


def _decode(raw: object, scope_digest: str) -> ApprovedParseWitness:
    try:
        if type(raw) is not str or not 0 < len(raw) <= _MAX_RECORD_BYTES:
            raise ValueError
        payload = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
        if type(payload) is not dict or set(payload) != {
            "schema", "generation", "baseline",
        } or type(payload["schema"]) is not int or payload["schema"] != 1:
            raise ValueError
        generation = payload["generation"]
        if type(generation) is not int or not 1 <= generation <= _MAX_GENERATION:
            raise ValueError
        baseline = payload["baseline"]
        if type(baseline) is not dict or set(baseline) != {
            "source_scope_sha256", "approved_input_sha256",
            "parser_version", "approved", "critical_fields",
        } or type(baseline["critical_fields"]) is not list:
            raise ValueError
        fields = []
        if not 1 <= len(baseline["critical_fields"]) <= 32:
            raise ValueError
        for item in baseline["critical_fields"]:
            if type(item) is not dict or set(item) != {
                "path", "expected_type", "required", "expected_sha256",
            } or type(item["path"]) is not list:
                raise ValueError
            fields.append(CriticalParseField(
                path=tuple(item["path"]),
                expected_type=item["expected_type"],
                required=item["required"],
                expected_sha256=item["expected_sha256"],
            ))
        witness = ParseWitness(
            source_scope_sha256=baseline["source_scope_sha256"],
            approved_input_sha256=baseline["approved_input_sha256"],
            parser_version=baseline["parser_version"],
            approved=baseline["approved"],
            critical_fields=tuple(fields),
        )
        if (not _valid_baseline(witness) or witness.approved is not True
                or witness.source_scope_sha256 != scope_digest):
            raise ValueError
        return ApprovedParseWitness(generation, witness)
    except (ValueError, TypeError, KeyError, AttributeError,
            UnicodeError, RecursionError) as exc:
        raise ValueError("corrupt_parse_witness_baseline") from exc


def read_approved_parse_witness(
    storage, *, key: NoticeSourceKey, parser_id: str,
) -> ApprovedParseWitness | None:
    """Read one exact account/source/parser baseline without any provider IO.

    The caller is responsible for authorization to consult the supplied key.
    Digest names are storage coordinates, not credentials or access grants.
    Parser version is deliberately NOT in the key: upgrades need comparison.
    """
    scope = admitted_source_scope_sha256(key, parser_id=parser_id)
    with storage._lock, storage._connect() as db:
        row = db.execute(
            "SELECT value FROM metadata WHERE key=?", (_PREFIX + scope,),
        ).fetchone()
    return _decode(row[0], scope) if row is not None else None


def publish_approved_parse_witness(
    storage, *, observation: NoticeSourceObservation,
    response_bytes: bytes, critical_fields: tuple[CriticalParseField, ...],
    expected_generation: int,
) -> ApprovedParseWitness:
    """Persist an explicitly approved baseline with cross-process CAS.

    The trusted Core caller must establish that response_bytes are the exact
    already-authorized bytes which produced observation, and obtain explicit
    baseline approval. This API never samples a provider or grants approval.
    A missing row requires expected_generation=0; subsequent replacements
    must name the exact stored generation. Same input is idempotent.
    """
    if (type(observation) is not NoticeSourceObservation
            or type(response_bytes) is not bytes
            or not 0 < len(response_bytes) <= _MAX_RESPONSE_BYTES
            or type(critical_fields) is not tuple
            or type(expected_generation) is not int
            or not 0 <= expected_generation <= _MAX_GENERATION):
        raise ValueError("invalid_parse_witness_publication")
    try:
        scope = admitted_source_scope_sha256(
            observation.key, parser_id=observation.parser_id,
        )
        baseline = ParseWitness(
            source_scope_sha256=scope,
            approved_input_sha256=sha256(response_bytes).hexdigest(),
            parser_version=observation.parser_version,
            approved=True,
            critical_fields=critical_fields,
        )
        if not _valid_baseline(baseline):
            raise ValueError
        # Re-admit the public DTO; it could have been altered since parsing.
        if type(observation.raw_json) is not str or len(observation.raw_json) > 65536:
            raise ValueError
        replay = admit_source_notice(
            key=observation.key, parser_id=observation.parser_id,
            parser_version=observation.parser_version,
            observed_at=observation.observed_at, status=observation.status,
            raw_fields=observation.fields_copy(),
        )
        if (replay.raw_json != observation.raw_json
                or replay.source_revision != observation.source_revision
                or observation.status != "complete"
                or review_admitted_source_parse_witness(
                    baseline, observation=observation,
                    input_sha256=baseline.approved_input_sha256,
                ).status != "unchanged"):
            raise ValueError
        encoded = json.dumps(
            {"schema": 1, "generation": 1, "baseline": asdict(baseline)},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        if len(encoded.encode("ascii")) > _MAX_RECORD_BYTES:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError,
            UnicodeError, RecursionError) as exc:
        raise ValueError("invalid_parse_witness_publication") from exc

    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT value FROM metadata WHERE key=?", (_PREFIX + scope,),
        ).fetchone()
        current = _decode(row[0], scope) if row is not None else None
        generation = current.generation if current is not None else 0
        if expected_generation != generation:
            raise ValueError("parse_witness_generation_changed")
        if current is not None and current.baseline == baseline:
            return current
        if generation == _MAX_GENERATION:
            raise ValueError("parse_witness_generation_overflow")
        next_generation = generation + 1
        raw = json.dumps(
            {"schema": 1, "generation": next_generation,
             "baseline": asdict(baseline)},
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        if len(raw.encode("ascii")) > _MAX_RECORD_BYTES:
            raise ValueError("invalid_parse_witness_publication")
        db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_PREFIX + scope, raw),
        )
        return ApprovedParseWitness(next_generation, baseline)


__all__ = [
    "ApprovedParseWitness", "read_approved_parse_witness",
    "publish_approved_parse_witness",
]
