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
from .notice_parse_witness import CriticalParseField, field_digest, review_admitted_source_parse_witness
from .notice_parse_witness_store import (
    _MAX_RESPONSE_BYTES, _PREFIX, _decode, _prepare_approved_parse_witness,
    _publish_approved_parse_witness_txn,
)
from .notice_parse_witness_alert import record_parse_witness_review_txn
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


def local_epub_witness(service, *, placement_id, scope, approved=None,
                       current=lambda: True):
    """Propose/review on explicit worker request, or approve an exact proposal.

    The existing witness input bound applies to this first local port.
    Never export book bytes or accept caller-supplied parse/digest claims.
    """
    if (type(scope) is not SearchReadScope or type(placement_id) is not str
            or not placement_id or (approved is not None
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
                    key = NoticeSourceKey(
                        "core.epub", "embedded", scope.identity_id, None, None,
                        "local.epub.metadata", artifact["artifact_id"],
                        "placement:" + sha256(placement_id.encode()).hexdigest(), "digital",
                    )
                    obs = admit_source_notice(
                        key=key, parser_id=PARSER, parser_version=str(epub_metadata.SCHEMA),
                        observed_at=int(time()), raw_fields=fields,
                        status="complete" if parsed["epub_status"] == "ok" else "partial",
                    )
                    from .notice_parse_witness import admitted_source_scope_sha256
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
                                record_parse_witness_review_txn(
                                    db, key=key, parser_id=PARSER,
                                    generation=baseline.generation, review=review,
                                )
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
                            fields["title"], tuple(authors),
                        )
                        if approved is None:
                            # Preserve a confirmed regression until explicitly
                            # reviewed; don't offer implicit reapproval.
                            return review if review and review.status != "unchanged" else proposal
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
