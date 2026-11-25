"""Q5/Q6: explicitly review an exact, authorized local EPUB using the real parser.

Only the existing witness CAS and alert transition may write. No changes to
the Artifact, Placement, catalogue, rights or file; no provider calls.
"""
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import os
import stat
from time import time

from .. import epub_metadata
from .library_inventory import _open_root, _fingerprint
from .notice_source_observation import NoticeSourceKey, admit_source_notice
from .notice_parse_witness import (CriticalParseField, field_digest, review_admitted_source_parse_witness,
                                  admitted_source_scope_sha256)
from .notice_parse_witness_store import (
    _MAX_RESPONSE_BYTES, _PREFIX, _decode, _prepare_approved_parse_witness,
    _publish_approved_parse_witness_txn,
)
from .notice_parse_witness_alert import (review_publication_txn, read_parse_witness_diagnostic,
                                        report_capture_diagnostic, _ALERT_PREFIX)
import json
from .notice_regression_capture_intent import export_capture
from .search_orchestration import SearchReadScope

PARSER = "core.epub.metadata"


@dataclass(frozen=True, slots=True, repr=False)
class EpubWitnessProposal:
    key: NoticeSourceKey
    generation: int
    source_epoch: int
    parser_version: str
    fields: tuple
    title: str
    authors: tuple
    revalidation: bool = False


@dataclass(frozen=True, slots=True)
class EpubWitnessReview:
    status: str
    affected_fields: tuple
    capture_state: str
    can_revalidate: bool


def local_epub_witness(service, *, placement_id, scope, approved=None,
                       current=lambda: True, revalidate=False):
    pending = []
    result = _review_local_epub(service, placement_id=placement_id, scope=scope,
                                approved=approved, current=current,
                                revalidate=revalidate, pending=pending)
    # Export after commit, outside source and SQLite locks; omit raw book.
    if pending:
        key, diagnostic, review, can_revalidate = pending[0]
        if (scope.current(service) and service.current_identity is scope.identity
                and current() and diagnostic is not None):
            try:
                export_capture(service.storage, diagnostic, members={},
                               directory=getattr(service, "_notice_regression_capture_directory", None))
            except (OSError, ValueError, RuntimeError):
                pass
        if not scope.current(service) or service.current_identity is not scope.identity or not current():
            return None
        state = read_parse_witness_diagnostic(service.storage, key=key, parser_id=PARSER)
        if type(result) is EpubWitnessProposal:
            return result
        return EpubWitnessReview(review.status, review.affected_fields,
                                 state["capture_state"] if state else "metadata_unavailable",
                                 can_revalidate)
    return result


def _review_local_epub(service, *, placement_id, scope, approved, current,
                       revalidate, pending):
    """Propose/review on explicit worker request, or approve an exact proposal.

    The existing witness input bound applies to this first local port.
    Never export book bytes or accept caller-supplied parse/digest claims.
    """
    if (type(scope) is not SearchReadScope or type(placement_id) is not str
            or not placement_id or type(revalidate) is not bool or (approved is not None
            and type(approved) is not EpubWitnessProposal)):
        return None

    def live():
        return (scope.current(service) and service.current_identity is scope.identity
                and current())

    with service._source_context_lock:
        if not live():
            return None
        placement = service.storage.placement_get(placement_id)
        if (not placement or placement["identity_id"] != scope.identity_id
                or placement["state"] != "present"):
            return None
        artifact = service.storage.artifact_get(placement["artifact_id"])
        if (not artifact or artifact["media_format"] not in ("epub", "kepub")
                or artifact["artifact_id"] != "sha256:" + artifact["sha256"]
                or type(artifact["size"]) is not int
                or not 0 < artifact["size"] <= _MAX_RESPONSE_BYTES):
            return None
        try:
            path = service.local_book_path(placement_id, identity_id=scope.identity_id)
            directory = _open_root(path.parent)
            try:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size != artifact["size"]:
                        return None
                    body = stream.read(_MAX_RESPONSE_BYTES + 1)
                    def unchanged():
                        return (_fingerprint(info) == _fingerprint(os.fstat(stream.fileno()))
                                == _fingerprint(os.stat(path.name, dir_fd=directory,
                                                        follow_symlinks=False))
                                == _fingerprint(os.stat(path, follow_symlinks=False)))
                    if (len(body) != info.st_size or not unchanged()
                            or sha256(body).hexdigest() != artifact["sha256"]):
                        return None
                    parsed = epub_metadata.inspect_epub(BytesIO(body), metadata_only=True)
                    if not unchanged() or not live():
                        return None
                    raw = parsed.get("epub_metadata", {})
                    fields = {name: raw[name] for name in ("title", "authors")
                              if name in raw}
                    key = _local_key(scope.identity_id, placement_id, artifact["artifact_id"])
                    obs = admit_source_notice(
                        key=key, parser_id=PARSER, parser_version=str(epub_metadata.SCHEMA),
                        observed_at=int(time()), raw_fields=fields,
                        status="complete" if parsed["epub_status"] == "ok" else "partial",
                    )
                    digest = admitted_source_scope_sha256(key, parser_id=PARSER)
                    with service.storage._lock, service.storage._connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        row = db.execute(
                            "SELECT artifact_id,identity_id,path,state FROM placements WHERE placement_id=?",
                            (placement_id,),
                        ).fetchone()
                        if (row is None or tuple(row) != (artifact["artifact_id"], scope.identity_id,
                                                         str(path), "present")
                                or not unchanged() or not live()):
                            return None
                        pin = db.execute("SELECT value FROM metadata WHERE key=?",
                                         (_PREFIX + digest,)).fetchone()
                        baseline = _decode(pin[0], digest) if pin is not None else None
                        review = None
                        if baseline is not None:
                            review = review_admitted_source_parse_witness(
                                baseline.baseline, observation=obs,
                                input_sha256=artifact["sha256"], publication_review=True,
                            )
                            if approved is None:
                                # Reuse a cached Artifact observation only if it
                                # independently matches the exact approved pin.
                                healthy = None
                                stored = artifact.get("metadata") or {}
                                if (stored.get("epub_status") == "ok"
                                        and str(stored.get("epub_metadata_schema")) == baseline.baseline.parser_version):
                                    previous = stored.get("epub_metadata") or {}
                                    try:
                                        candidate = admit_source_notice(
                                            key=key, parser_id=PARSER,
                                            parser_version=baseline.baseline.parser_version,
                                            observed_at=obs.observed_at,
                                            raw_fields={n: previous[n] for n in ("title", "authors") if n in previous},
                                        )
                                        if review_admitted_source_parse_witness(
                                            baseline.baseline, observation=candidate,
                                            input_sha256=artifact["sha256"],
                                        ).status == "unchanged":
                                            healthy = candidate
                                    except (ValueError, TypeError, KeyError):
                                        pass
                                rejected, diagnostic = review_publication_txn(
                                    db, current=healthy, candidate=obs,
                                    input_digest=artifact["sha256"], implementation_digest=None,
                                )
                                if rejected:
                                    can_revalidate = (obs.status == "complete"
                                                      and type(fields.get("title")) is str
                                                      and bool(fields["title"]))
                                    pending.append((key, diagnostic, review, can_revalidate))
                        if (obs.status != "complete" or type(fields.get("title")) is not str
                                or not fields["title"]):
                            return review
                        authors = fields.get("authors", [])
                        if type(authors) is not list or any(type(a) is not str for a in authors):
                            return review
                        critical = [CriticalParseField(("title",), "string",
                                                       expected_sha256=field_digest(fields["title"]))]
                        if authors:
                            critical.append(CriticalParseField(("authors",), "array",
                                                               expected_sha256=field_digest(authors)))
                        proposal = EpubWitnessProposal(
                            key, baseline.generation if baseline else 0,
                            scope.source_epoch, obs.parser_version, tuple(critical),
                            fields["title"], tuple(authors), revalidate,
                        )
                        if approved is None:
                            # Preserve a confirmed regression until explicitly
                            # reviewed; don't offer implicit reapproval.
                            return review if review and review.status != "unchanged" and not revalidate else proposal
                        if proposal != approved or not live():
                            return None
                        digest, new = _prepare_approved_parse_witness(
                            observation=obs, response_bytes=body,
                            critical_fields=proposal.fields,
                            expected_generation=proposal.generation,
                        )
                        return _publish_approved_parse_witness_txn(
                            db, digest, new, expected_generation=proposal.generation,
                        )
            finally:
                os.close(directory)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            return None


def _local_key(identity_id, placement_id, artifact_id):
    return NoticeSourceKey(
        "core.epub", "embedded", identity_id, None, None,
        "local.epub.metadata", artifact_id,
        "placement:" + sha256(placement_id.encode()).hexdigest(), "digital",
    )


def _catalog_observation(key, inspected, *, at):
    raw = inspected.get("epub_metadata") or {}
    return admit_source_notice(
        key=key, parser_id=PARSER,
        parser_version=str(inspected.get("epub_metadata_schema")),
        observed_at=at, raw_fields={n: raw[n] for n in ("title", "authors") if n in raw},
        status="complete" if inspected.get("epub_status") == "ok" else "partial",
    )


def _catalog_pins(db, artifact_id):
    """Point-read known placement pins of this exact shared Artifact only."""
    rows = db.execute(
        "SELECT placement_id,identity_id,path,state FROM placements WHERE artifact_id=? LIMIT 257",
        (artifact_id,),
    ).fetchall()
    if len(rows) > 256:
        raise ValueError("epub_witness_placement_budget")
    from .notice_source_observation import _valid_key
    pins = []
    for placement, owner, path, state in rows:
        key = _local_key(owner, placement, artifact_id)
        if not _valid_key(key):
            continue
        digest = admitted_source_scope_sha256(key, parser_id=PARSER)
        pin = db.execute("SELECT value FROM metadata WHERE key=?", (_PREFIX + digest,)).fetchone()
        if pin is not None:
            pins.append((placement, owner, path, state, key, digest, _decode(pin[0], digest)))
    return pins


def refresh_catalog_epub(storage, artifact, entry, path, *, parse,
                         directory=None, diagnostics=None):
    """One normal parse, guarded at the existing Artifact writer transaction.

    The shared Artifact cannot overwrite any attached approved witness.
    Only the actual inventoried owner/placement may publish its own alert.
    A concurrent pin/placement or metadata change refuses the write.
    """
    aid = artifact["artifact_id"]
    cached = artifact["metadata"]
    pending = []
    try:
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN")
            pinned = bool(_catalog_pins(db, aid))
        body = None
        stamp = None
        if pinned:
            # Do not read arbitrary large files into memory. The existing
            # explicit witness approval uses this same exact-byte bound.
            if not 0 < artifact["size"] <= _MAX_RESPONSE_BYTES:
                return cached, False
            parent = _open_root(path.parent)
            try:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size != artifact["size"]:
                        return cached, False
                    body = stream.read(_MAX_RESPONSE_BYTES + 1)
                    stamp = _fingerprint(info)
                    if (len(body) != info.st_size or sha256(body).hexdigest() != artifact["sha256"]
                            or aid != "sha256:" + artifact["sha256"]
                            or _fingerprint(os.fstat(stream.fileno())) != stamp
                            or _fingerprint(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != stamp):
                        return cached, False
            finally:
                os.close(parent)
        inspected = parse(BytesIO(body) if body is not None else path)
        merged = {**cached, **inspected}
        if not inspected.get("epub_cover"):
            merged.pop("epub_cover", None)

        def guard(db):
            row = db.execute(
                "SELECT sha256,size,media_format,metadata_json FROM artifacts WHERE artifact_id=?",
                (aid,),
            ).fetchone()
            if (row is None or tuple(row[:3]) != (artifact["sha256"], artifact["size"], artifact["media_format"])
                    or storage._json_load(row[3]) != cached):
                return False
            pins = _catalog_pins(db, aid)
            if not pins:
                return True
            if body is None or stamp != _fingerprint(os.stat(path, follow_symlinks=False)):
                return False
            allowed = True
            at = int(time())
            for placement, owner, named, state, key, digest, pin in pins:
                obs = _catalog_observation(key, inspected, at=at)
                review = review_admitted_source_parse_witness(
                    pin.baseline, observation=obs, input_sha256=artifact["sha256"],
                    publication_review=True,
                )
                if (review.status != "unchanged" or obs.status != "complete"
                        or pin.baseline.approved_input_sha256 != artifact["sha256"]):
                    allowed = False
                # A separate profile's pin is not authority to inspect or
                # export its diagnostic from this Placement.
                if (placement, owner, named, state) != (
                    entry.get("placement_id"), entry.get("identity_id"), str(path), "present",
                ):
                    continue
                healthy = None
                if str(cached.get("epub_metadata_schema")) == pin.baseline.parser_version:
                    previous = _catalog_observation(key, cached, at=at)
                    if review_admitted_source_parse_witness(
                        pin.baseline, observation=previous, input_sha256=artifact["sha256"],
                    ).status == "unchanged":
                        healthy = previous
                prior = db.execute("SELECT value FROM metadata WHERE key=?",
                                   (_ALERT_PREFIX + digest,)).fetchone()
                rejected, diagnostic = review_publication_txn(
                    db, current=healthy, candidate=obs, input_digest=artifact["sha256"],
                    implementation_digest=None,
                )
                if rejected:
                    old = json.loads(prior[0]) if prior else {}
                    pending.append((diagnostic, old.get("capture_id") == diagnostic
                                    and old.get("generation") == pin.generation
                                    and old.get("status") == review.status
                                    and old.get("fields") == sorted(review.affected_fields)))
            return allowed

        committed = storage.artifact_put({**artifact, "metadata": merged},
                                         _catalog_epub_guard=guard)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return cached, False
    for diagnostic, repeated in pending:
        state = "capacity_refused" if diagnostic is None else export_capture(
            storage, diagnostic, members={}, directory=directory,
        )
        if state is not None and not repeated:
            report_capture_diagnostic(diagnostics, diagnostic, state, rejected=True)
    # On rejection use current healthy metadata, not the discarded candidate.
    current = storage.artifact_get(aid)
    return ((current or artifact)["metadata"], committed is not None)
