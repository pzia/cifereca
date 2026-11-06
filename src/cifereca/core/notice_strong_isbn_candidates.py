"""#2385/#4552: bounded, per-identity proposed same-manifestation ISBN links.

An OWN identifier attested by an audited source parser is distinct from
associated print/digital format ISBNs or an ISBN embedded in a search string.
This pure planner never writes NoticeRelations or consolidates source rows;
the future Core writer must separately authenticate every parser claim,
confirm authority/scope and link provenance before persisting any relation.
No acquisition rights, loan or ownership can be inherited from a link.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import re

from ..plugin_api.book_references import _isbn

_MAX_FACTS = 512
_MAX_RELATIONS = 8192
_MAX_REF = 160


@dataclass(frozen=True, slots=True)
class OwnIsbnClaim:
    identity_id: str
    source_id: str
    notice_id: str
    edition_id: str
    evidence_ref: str
    namespace: str  # isbn10 | isbn13; aliases of the same ISBN authority
    raw_value: str
    manifestation_role: str  # print | digital | other | unknown
    own_identifier: bool


@dataclass(frozen=True, slots=True)
class CandidateSameManifestation:
    identity_id: str
    isbn13: str
    manifestation_role: str
    left: tuple[str, str, str]  # source, notice, edition
    right: tuple[str, str, str]
    left_evidence_refs: tuple[str, ...]
    right_evidence_refs: tuple[str, ...]
    relation: str = "same_manifestation_candidate"


def _reference(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= _MAX_REF
            and value == value.strip() and value.isprintable()
            and all(ch.isascii() and (ch.isalnum() or ch in "._:-/")
                    for ch in value)
            and all(part not in (".", "..") for part in value.split("/")))


def _isbn13(namespace: str, raw: str) -> str:
    if namespace not in ("isbn10", "isbn13") or type(raw) is not str:
        return ""
    if not 10 <= len(raw) <= 32 or re.fullmatch(r"[0-9Xx\- ]+", raw) is None:
        return ""
    validated = _isbn(raw)
    if not validated or len(validated) != (10 if namespace == "isbn10" else 13):
        return ""
    if len(validated) == 13:
        return validated
    # The 978 prefix converts a checksum-valid ISBN-10 to the equivalent
    # manifestation's ISBN-13 (never to a different ISBN or a 979 variant).
    stem = "978" + validated[:9]
    digit = (10 - sum(
        int(ch) * (1 if index % 2 == 0 else 3)
        for index, ch in enumerate(stem)
    ) % 10) % 10
    return stem + str(digit)


def propose_same_manifestation_isbn_links(
    *, identity_id: str, claims: object,
) -> tuple[CandidateSameManifestation, ...]:
    """Return deterministic candidates requiring subsequent Core attestation.

    Never connect unknown-role, different media roles, absent/associated
    identifiers, foreign identity rows, or a notice claiming conflicting
    OWN ISBNs/roles. A conflicting notice rejects the whole snapshot rather
    than allowing claim ordering to choose the identifier.
    """
    if (not _reference(identity_id) or type(claims) not in (tuple, list)
            or len(claims) > _MAX_FACTS):
        raise ValueError("invalid_isbn_candidate_batch")
    notices: dict[tuple[str, str], tuple[tuple, set[str]]] = {}
    for claim in claims:
        if type(claim) is not OwnIsbnClaim:
            raise ValueError("invalid_isbn_claim")
        # Foreign claims, associated ISBNs, and unknown roles are deliberately
        # not evidence for a current reader's same-manifestation link.
        if (claim.identity_id != identity_id or claim.own_identifier is not True
                or claim.manifestation_role not in ("print", "digital")):
            continue
        if not all(_reference(x) for x in (
                claim.source_id, claim.notice_id,
                claim.edition_id, claim.evidence_ref)):
            raise ValueError("invalid_own_isbn_provenance")
        canonical = _isbn13(claim.namespace, claim.raw_value)
        if not canonical:
            continue
        key = (claim.source_id, claim.notice_id)
        coordinates = (canonical, claim.manifestation_role, claim.edition_id)
        old = notices.get(key)
        if old is not None:
            if old[0] != coordinates:
                raise ValueError("conflicting_own_isbn_claim")
            old[1].add(claim.evidence_ref)
        else:
            notices[key] = (coordinates, {claim.evidence_ref})
    groups: dict[tuple[str, str], list[tuple[tuple[str, str], str, tuple[str, ...]]]] = {}
    for key, (coordinates, refs) in notices.items():
        isbn, role, edition = coordinates
        groups.setdefault((isbn, role), []).append(
            (key, edition, tuple(sorted(refs)))
        )
    result: list[CandidateSameManifestation] = []
    for (isbn, role), items in sorted(groups.items()):
        ordered = sorted(items)
        for (lk, le, lr), (rk, re, rr) in combinations(ordered, 2):
            if len(result) >= _MAX_RELATIONS:
                raise ValueError("too_many_isbn_relation_candidates")
            result.append(CandidateSameManifestation(
                identity_id=identity_id,
                isbn13=isbn, manifestation_role=role,
                left=(lk[0], lk[1], le), right=(rk[0], rk[1], re),
                left_evidence_refs=lr, right_evidence_refs=rr,
            ))
    return tuple(result)


__all__ = [
    "OwnIsbnClaim", "CandidateSameManifestation",
    "propose_same_manifestation_isbn_links",
]
