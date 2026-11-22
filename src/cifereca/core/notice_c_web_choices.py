"""#6397: bounded public C web choices and exact click-time resolution.

Core read-only worker port. It does not open a browser or perform HTTP,
resolve an authenticated locator, consent to acquisition, or act on GTK.
The caller must still fence its own view/identity generation at delivery.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re

from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import _db_key, _decode, is_canonical_cover_document
from .notice_c_web_references import FIELD, decode_public_web_references, qualified_public_web_source
from .notice_cover_repository import (
    _opaque_key, _checked_source_state, _source_fingerprint,
)

_ID = re.compile(r"[0-9a-f]{64}\Z")
_FIELD_PATH = ("search_result", "thumbnail_url")


@dataclass(frozen=True, slots=True)
class PublicWebChoice:
    choice_id: str
    provider_id: str
    record_id: str
    edition_id: str
    observed_at: int
    label: str


@dataclass(frozen=True, slots=True)
class PublicWebChoices:
    document: CoverDocumentScope
    epoch: int
    choices: tuple[PublicWebChoice, ...]
    # Unknown/no qualified options is not a complete negative inventory.


def _choice_id(document, epoch, ref):
    return sha256(json.dumps(
        ["cifereca.public-web-choice.v1", document.source_key, epoch,
         ref.source_ref, ref.source_revision, ref.url],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")).hexdigest()


def _current_refs(db, source):
    """Do not replay retained links over superseding source evidence."""
    refs = source.public_web_references
    if not refs:
        return ()
    doc = CoverDocumentScope(
        kind="public_catalogue", source_key=source.source_ref,
        identity_id=None, provider_id=source.provider_id, credential_id="__public__",
    )
    key = _opaque_key(doc)
    raw, live, fence = _checked_source_state(db, doc, key)
    if live is None:
        # A qualified retention eviction of THIS exact observation remains
        # eligible. A raw DELETE, missing fence or newer evicted revision
        # cannot authorize the URL. Verify both generation and fingerprint.
        if (fence is None or fence.get("evicted") is not True
                or fence["epoch"] != source.source_epoch + 1
                or fence["proof"] != _source_fingerprint(refs[0], _FIELD_PATH, True)):
            return ()
        return refs
    observation = live.observation
    if (live.epoch != source.source_epoch
            or observation.source_revision != source.source_revision
            or live.attribute.state != source.state
            or not qualified_public_web_source(
                live, inventory_complete=json.loads(raw[0])["cover_inventory_complete"],
            )):
        return ()
    fields = observation.fields_copy().get("search_result")
    if type(fields) is not dict or FIELD not in fields:
        return ()
    bib = source.public_bibliography
    actual = decode_public_web_references(
        fields[FIELD], provider_id=source.provider_id, source_ref=source.source_ref,
        record_id=bib.record_id, edition_id=bib.edition_id,
        source_revision=observation.source_revision, observed_at=observation.observed_at,
    )
    return refs if actual == refs else ()


def _read(db, document):
    raw = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
    if raw is None:
        return None, ()
    snapshot = _decode(raw[0], document)
    pairs = []
    for source in snapshot.sources:
        try:
            refs = _current_refs(db, source)
        except (ValueError, TypeError, KeyError, AttributeError):
            # A corrupt source observation/fence disqualifies only that
            # exact contribution. Other sources in the decoded canonical
            # snapshot still require their own independent current proof.
            # Do not suppress a corrupt canonical row or SQLite failures.
            continue
        for ref in refs:
            choice = PublicWebChoice(
                _choice_id(document, snapshot.epoch, ref), ref.provider_id,
                ref.record_id, ref.edition_id, ref.observed_at,
                f"{ref.provider_id} — {ref.record_id}",
            )
            pairs.append((choice, ref))
    # Number across all source revisions, not just within one source.
    # The same provider/record can contribute multiple exact references to C.
    # Do not expose a URL or an opaque revision token in the menu.
    counts = Counter(choice.label for choice, _ in pairs)
    ordinals = Counter()
    labelled = []
    for choice, ref in pairs:
        if counts[choice.label] > 1:
            ordinals[choice.label] += 1
            choice = replace(choice, label=(
                f"{choice.label} ({ordinals[choice.label]}/{counts[choice.label]})"
            ))
        labelled.append((choice, ref))
    return snapshot, tuple(labelled)


def read_public_web_choices(storage, document):
    """Read one canonical document and exact source fences, no provider I/O."""
    if not is_canonical_cover_document(document):
        return None
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        snapshot, pairs = _read(db, document)
        if snapshot is None:
            return None
        return PublicWebChoices(document, snapshot.epoch,
                                tuple(choice for choice, _ in pairs))


def resolve_public_web_choice(storage, document, *, expected_epoch, choice_id):
    """Re-read a selected opaque option in one SQLite snapshot.

    Returns only a stable public URL if the displayed C revision and exact
    source observation still qualify. Never honors a caller-provided URL.
    On refusal return None; caller may refresh the menu, not substitute.
    """
    if (not is_canonical_cover_document(document)
            or type(expected_epoch) is not int or not 1 <= expected_epoch < 2**63
            or type(choice_id) is not str or _ID.fullmatch(choice_id) is None):
        return None
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        snapshot, pairs = _read(db, document)
        if snapshot is None or snapshot.epoch != expected_epoch:
            return None
        matches = [ref.url for choice, ref in pairs if choice.choice_id == choice_id]
        return matches[0] if len(matches) == 1 else None


__all__ = ["PublicWebChoice", "PublicWebChoices", "read_public_web_choices",
           "resolve_public_web_choice"]
