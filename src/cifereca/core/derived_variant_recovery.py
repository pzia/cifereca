"""#4520: durable, identity-scoped receipts for FS-before-SQL derivative recovery.

No untrusted pathname, content SHA or global variant recipe alone is authority.
Recovery is explicit (never on startup), bounded and non-destructive.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any

from .derived_variant_publication import publish_derived_variant

SCHEMA = "cifereca.derived-publication-pending/1"
MAX_RECEIPTS = 128
MAX_BYTES = 8192
MAX_SCAN_DIRECTORIES = 8192
MAX_SCAN_DEPTH = 32
# Conservative local safety valve until Core-private sealed storage is available.
# This is per staging directory, not a global quota across all book folders.
MAX_RECEIPTS_PER_STAGING_DIRECTORY = 1024


def _stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        s = path.lstat()
        if stat.S_ISREG(s.st_mode):
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    except OSError:
        pass
    return None


def _open_under_root_nofollow(root: Path, path: Path, *,
                              directory: bool = False) -> int:
    """Open each path component relative to a pinned profile FD.

    O_NOFOLLOW on only the final file does not protect intermediate symlink
    substitutions. This chain refuses every symlink component and never uses
    the ambient working directory or a path outside the approved profile.
    """
    relative = path.relative_to(root)
    parts = relative.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("derived_recovery_invalid_relative_path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    dir_flags = flags | os.O_DIRECTORY
    fd = os.open(root, dir_flags)
    try:
        for index, part in enumerate(parts):
            next_fd = os.open(
                part, dir_flags if index + 1 < len(parts) or directory else flags,
                dir_fd=fd,
            )
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _verified_sha(path: Path, expected_stamp: tuple[int, ...],
                  expected_sha: str, *, profile_root: Path | None = None) -> bool:
    """Hash exactly the O_NOFOLLOW inode, checking fstat before/after read."""
    if (_stamp(path) != expected_stamp or type(expected_sha) is not str
            or len(expected_sha) != 64):
        return False
    try:
        fd = (
            _open_under_root_nofollow(profile_root, path)
            if profile_root is not None
            else os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        )
        try:
            before = os.fstat(fd)
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if not stat.S_ISREG(before.st_mode) or signature(before) != expected_stamp:
                return False
            hasher = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                hasher.update(chunk)
            after = os.fstat(fd)
            return (signature(after) == signature(before)
                    and _stamp(path) == expected_stamp
                    and hasher.hexdigest() == expected_sha)
        finally:
            os.close(fd)
    except (OSError, ValueError):
        return False


def _read_receipt_nofollow(
    journal: Path, *, profile_root: Path | None = None,
) -> bytes:
    """Read a bounded, stable regular file, never an attacker-controlled link."""
    expected = _stamp(journal)
    if expected is None or expected[2] > MAX_BYTES:
        raise ValueError("derived_receipt_invalid_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = (
        _open_under_root_nofollow(profile_root, journal)
        if profile_root is not None
        else os.open(journal, flags)
    )
    try:
        before = os.fstat(fd)
        signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if (not stat.S_ISREG(before.st_mode)
                or signature(before) != expected):
            raise ValueError("derived_receipt_changed")
        raw = os.read(fd, MAX_BYTES + 1)
        if (len(raw) != expected[2] or len(raw) > MAX_BYTES
                or signature(os.fstat(fd)) != expected
                or _stamp(journal) != expected):
            raise ValueError("derived_receipt_changed")
        return raw
    finally:
        os.close(fd)


def _directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except OSError:
        return False


def write_pending_derived_receipt(staged: Path, published: Path, *,
                                  stage_dir_fd: int,
                                  source_path: Path, source_stamp: tuple[int, ...],
                                  source_artifact_id: str,
                                  source_sha256: str, source_placement_id: str,
                                  identity_id: str, output_sha256: str,
                                  output_size: int, media_format: str,
                                  mime_type: str | None, variant_kind: str,
                                  transformer_id: str, transformer_version: str,
                                  profile_id: str | None, profile_version: str,
                                  options_fingerprint: str, book_id: str | None,
                                  warnings: tuple[str, ...] | list[str],
                                  facts: dict[str, Any]) -> tuple[
                                      Path, str, tuple[int, int, int, int],
                                  ]:
    """fsync output, receipt, and directory BEFORE exclusive hardlink creation."""
    if (_stamp(staged) is None or not _directory(staged.parent)
            or _stamp(source_path) != tuple(source_stamp[:4])
            or not _directory(published.parent)
            or published.parent != source_path.parent):
        raise ValueError("derived_receipt_scope_unverified")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    # Use the already pinned O_DIRECTORY descriptor from the publisher:
    # a renamed staging parent can never redirect a receipt into another
    # profile's path after the preliminary pathname checks.
    if not stat.S_ISDIR(os.fstat(stage_dir_fd).st_mode):
        raise ValueError("derived_receipt_staging_changed")
    fd = os.open(staged.name, flags, dir_fd=stage_dir_fd)
    try:
        st = os.fstat(fd)
        named = os.stat(staged.name, dir_fd=stage_dir_fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or (
            st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns
        ) != (named.st_dev, named.st_ino, named.st_size, named.st_mtime_ns):
            raise ValueError("derived_receipt_staging_changed")
        os.fsync(fd)
        proof = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
    finally:
        os.close(fd)
    payload = {
        "schema": SCHEMA, "source_path": str(source_path),
        "source_stamp": list(source_stamp[:4]),
        "source_artifact_id": source_artifact_id, "source_sha256": source_sha256,
        "source_placement_id": source_placement_id, "identity_id": identity_id,
        "published_path": str(published), "output_sha256": output_sha256,
        "output_size": output_size, "output_stamp": proof,
        "media_format": media_format, "mime_type": mime_type,
        "variant_kind": variant_kind, "transformer_id": transformer_id,
        "transformer_version": transformer_version,
        "profile_id": profile_id, "profile_version": profile_version,
        "options_fingerprint": options_fingerprint, "book_id": book_id,
        "warnings": list(warnings), "facts": facts,
    }
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("derived_receipt_invalid") from exc
    if len(raw) > MAX_BYTES:
        raise ValueError("derived_receipt_too_large")
    # No unlink-based eviction: a malicious or concurrent profile writer
    # could replace a receipt name between inode validation and unlink.
    # Fail before creating a new receipt/intent or destination hardlink when
    # this local staging directory has exhausted its retention budget.
    # Counting by pinned dirfd prevents following replacement directory links.
    receipt_count = 0
    with os.scandir(stage_dir_fd) as entries:
        for entry in entries:
            if (entry.name.startswith("pending-")
                    and entry.name.endswith(".json")):
                receipt_count += 1
                if receipt_count >= MAX_RECEIPTS_PER_STAGING_DIRECTORY:
                    raise ValueError("derived_receipt_retention_capacity_exceeded")
    journal = staged.parent / ("pending-" + secrets.token_hex(16) + ".json")
    fd = os.open(journal.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=stage_dir_fd)
    try:
        with os.fdopen(fd, "wb", closefd=True) as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
            signed = os.fstat(file.fileno())
            receipt_stamp = (
                signed.st_dev, signed.st_ino,
                signed.st_size, signed.st_mtime_ns,
            )
    except Exception:
        # An incomplete receipt has no SQL intent. Leave it for manual
        # review rather than unlinking a possibly substituted file.
        raise
    os.fsync(stage_dir_fd)
    return journal, hashlib.sha256(raw).hexdigest(), receipt_stamp


_INTENT_KEY = "derived.publication.pending.v1:"


def _pending_intent_key(journal: Path) -> str:
    name = journal.name
    nonce = name.removeprefix("pending-").removesuffix(".json")
    if (not name.startswith("pending-") or not name.endswith(".json")
            or len(nonce) != 32
            or any(ch not in "0123456789abcdef" for ch in nonce)):
        raise ValueError("derived_receipt_name_invalid")
    return _INTENT_KEY + nonce


def register_pending_derived_receipt(
    storage: Any, journal: Path, *, expected_sha256: str,
) -> None:
    """Bind the ORIGINAL bytes written by Core; reject replacement before SQL."""
    raw = _read_receipt_nofollow(journal)
    digest = hashlib.sha256(raw).hexdigest()
    if (type(expected_sha256) is not str
            or digest != expected_sha256):
        raise ValueError("derived_receipt_changed_before_intent")
    key = _pending_intent_key(journal)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        result = db.execute(
            "INSERT INTO metadata(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO NOTHING",
            (key, digest),
        )
        if result.rowcount != 1:
            raise ValueError("derived_receipt_intent_collision")


def _intent_matches(storage: Any, journal: Path, digest: str) -> bool:
    """A filesystem receipt alone can never authorize new derived history."""
    try:
        key = _pending_intent_key(journal)
        return storage.metadata_get(key) == digest
    except (TypeError, ValueError):
        return False


def review_pending_derived_receipts(service: Any, *, identity_id: str,
                                    commit: bool = False,
                                    approved_receipt_sha256: str = "",
                                    after_cursor: str = "") -> tuple[dict[str, str], ...]:
    """Explicit bounded review/recovery. No ambiguous evidence is promoted.

    The journal is NOT a provider right. For commit, an operator must approve
    the SHA256 of the exact reviewed receipt bytes; no unattended bulk replay.
    """
    if (type(after_cursor) is not str or len(after_cursor) > 512
            or chr(0) in after_cursor):
        raise ValueError("derived_recovery_cursor_invalid")
    storage = service.storage
    if commit and getattr(getattr(service, "current_identity", None), "identity_id", None) != identity_id:
        raise ValueError("derived_recovery_active_identity_mismatch")
    profile = storage.identity_get(identity_id)
    config = getattr(service, "config", None)
    get = getattr(config, "get", None)
    raw_root = get("library.directory", None) if callable(get) else None
    dirname = profile.get("directory_name") if type(profile) is dict else None
    if (type(identity_id) is not str or not identity_id
            or type(raw_root) is not str or not raw_root
            or type(dirname) is not str or not dirname or dirname in (".", "..")
            or "/" in dirname or "\\" in dirname):
        raise ValueError("derived_recovery_invalid_scope")
    root = Path(os.path.expandvars(raw_root)).expanduser()
    folder = root / dirname
    if not root.is_absolute() or not _directory(root) or not _directory(folder):
        raise ValueError("derived_recovery_invalid_root")
    # No follow: an untrusted nested symlink cannot redirect the scanner.
    # Only the exact profile root and its direct child folders are examined.
    if (root.is_symlink() or root.resolve() != root
            or folder.is_symlink() or folder.resolve() != folder
            or sum(row.get("directory_name") == dirname
                   for row in storage.identities()) != 1):
        raise ValueError("derived_recovery_invalid_root")
    # Cursor pagination is non-destructive and memory bounded. Successful
    # receipts are retained, so a global 128-file ceiling would otherwise
    # render recovery unusable after 128 ordinary transformations.
    def entries_nofollow(directory: Path):
        # Pin the complete path from the trusted profile root. scandir(fd)
        # cannot be redirected if a concurrent actor swaps an intermediate
        # directory for a symlink after enumeration starts.
        fd = (
            os.open(
                folder, os.O_RDONLY | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0),
            )
            if directory == folder
            else _open_under_root_nofollow(folder, directory, directory=True)
        )
        try:
            with os.scandir(fd) as iterator:
                for entry in iterator:
                    yield entry.name, entry.is_dir(follow_symlinks=False)
        finally:
            os.close(fd)

    def iter_receipts():
        pending = [(folder, 0)]
        visited = 0
        while pending:
            subdir, depth = pending.pop()
            visited += 1
            if visited > MAX_SCAN_DIRECTORIES:
                raise ValueError("derived_recovery_directory_limit")
            journal_root = subdir / ".cifereca-staging"
            journal_dir = journal_root / "variants"
            if (_directory(journal_root) and not journal_root.is_symlink()
                    and _directory(journal_dir)
                    and not journal_dir.is_symlink()):
                for name, _ in entries_nofollow(journal_dir):
                    if name.startswith("pending-") and name.endswith(".json"):
                        file = journal_dir / name
                        key = file.relative_to(folder).as_posix()
                        if len(key) > 512:
                            raise ValueError("derived_recovery_cursor_limit")
                        if key > after_cursor:
                            yield key, file

            for name, is_dir in entries_nofollow(subdir):
                if name == ".cifereca-staging":
                    continue
                if is_dir:
                    if depth >= MAX_SCAN_DEPTH:
                        raise ValueError("derived_recovery_depth_limit")
                    pending.append((subdir / name, depth + 1))

    page = heapq.nsmallest(
        MAX_RECEIPTS + 1, iter_receipts(), key=lambda item: item[0],
    )
    has_more = len(page) > MAX_RECEIPTS
    if has_more:
        page.pop()
    out: list[dict[str, str]] = []
    for key, journal in page:
        outcome = {"receipt": journal.name, "cursor": key,
                   "status": "manual_review"}
        out.append(outcome)
        try:
            receipt_bytes = _read_receipt_nofollow(
                journal, profile_root=folder,
            )
            receipt_digest = hashlib.sha256(receipt_bytes).hexdigest()
            data = json.loads(receipt_bytes)
            if (type(data) is not dict or data.get("schema") != SCHEMA
                    or data.get("identity_id") != identity_id):
                continue
            source = Path(data["source_path"])
            target = Path(data["published_path"])
            if (source.parent != journal.parent.parent.parent
                    or target.parent != source.parent
                    or source.is_symlink() or target.is_symlink()
                    or source.resolve(strict=True) != source
                    or target.resolve(strict=True) != target
                    or not source.is_relative_to(folder)
                    or not target.is_relative_to(folder)
                    or not isinstance(data.get("output_stamp"), list)
                    or len(data["output_stamp"]) != 4
                    or type(data.get("output_sha256")) is not str
                    or len(data["output_sha256"]) != 64
                    or data.get("source_artifact_id") != "sha256:" + data.get("source_sha256", "")):
                continue
            source_stamp = _stamp(source)
            expected_source_stamp = data.get("source_stamp")
            if (not isinstance(expected_source_stamp, list)
                    or len(expected_source_stamp) != 4
                    or any(type(value) is not int for value in expected_source_stamp)
                    or source_stamp != tuple(expected_source_stamp)):
                continue
            output_stamp = tuple(data["output_stamp"])
            if (type(data.get("output_size")) is not int
                    or data["output_size"] <= 0
                    or data["output_size"] != output_stamp[2]):
                continue
            if (source_stamp is None
                    or not _verified_sha(
                        target, output_stamp, data["output_sha256"],
                        profile_root=folder,
                    )
                    or not _verified_sha(
                        source, source_stamp, data["source_sha256"],
                        profile_root=folder,
                    )):
                continue
            parent = storage.placement_get(data["source_placement_id"])
            if (parent is None or parent.get("identity_id") != identity_id
                    or parent.get("artifact_id") != data["source_artifact_id"]
                    or parent.get("path") != str(source)
                    or parent.get("state") != "present"):
                continue
            claimed = storage.placement_for_path(target)
            if claimed is not None:
                if (claimed.get("artifact_id") == "sha256:" + data["output_sha256"]
                        and claimed.get("identity_id") == identity_id
                        and claimed.get("provider_id") == "cifereca"
                        and (claimed.get("metadata") or {}).get("source_placement_id")
                            == data["source_placement_id"]):
                    recipe_id = storage._stable_id(
                        "variant", data["source_sha256"], data["transformer_id"],
                        str(data["profile_id"] or ""),
                        str(data["options_fingerprint"] or ""),
                    )
                    proof = ("derived:" + recipe_id + ":"
                             + str(claimed["placement_id"]))
                    with storage._lock, storage._connect() as db:
                        attested = db.execute(
                            "SELECT 1 FROM origin_links WHERE origin_kind='derived' "
                            "AND identity_id=? AND artifact_id=? "
                            "AND parent_artifact_id=? AND evidence_ref=? LIMIT 1",
                            (identity_id, claimed["artifact_id"],
                             data["source_artifact_id"], proof),
                        ).fetchone()
                    if attested:
                        # Canonical derived OriginLink remains authoritative
                        # after a supervised SQL-intent compaction. This
                        # read-only status never creates a new provenance.
                        outcome["status"] = "already_committed"
                        outcome["sha256"] = receipt_digest
                continue
            if not _intent_matches(storage, journal, receipt_digest):
                # Without the original prelink intent, an orphan file/JSON
                # can never be promoted. A committed child is recognized
                # only by the exact existing Placement and OriginLink above.
                continue
            outcome["status"] = "recoverable"
            outcome["sha256"] = receipt_digest
            if not commit:
                continue
            if (type(approved_receipt_sha256) is not str
                    or approved_receipt_sha256 != receipt_digest):
                outcome["status"] = "approval_required"
                continue
            # Exact operator-approved journal, checked again before SQL.
            if (not _verified_sha(
                        target, output_stamp, data["output_sha256"],
                        profile_root=folder,
                    )
                    or not _verified_sha(
                        source, source_stamp, data["source_sha256"],
                        profile_root=folder,
                    )):
                outcome["status"] = "manual_review"
                continue
            publish_derived_variant(
                storage, parent_artifact_id=data["source_artifact_id"],
                parent_sha256=data["source_sha256"],
                parent_placement_id=data["source_placement_id"],
                parent_path=source, identity_id=identity_id,
                published_path=target, output_sha256=data["output_sha256"],
                output_size=data["output_size"],
                media_format=data["media_format"], mime_type=data["mime_type"],
                variant_kind=data["variant_kind"], transformer_id=data["transformer_id"],
                transformer_version=data["transformer_version"],
                profile_id=data["profile_id"], profile_version=data["profile_version"],
                options_fingerprint=data["options_fingerprint"],
                book_id=data["book_id"], warnings=data["warnings"], facts=data["facts"],
                expected_intent_key=_pending_intent_key(journal),
                expected_intent_sha256=receipt_digest,
                expected_parent_stamp=source_stamp,
                expected_output_stamp=output_stamp,
                expected_profile_root=folder,
            )
            outcome["status"] = "recovered"
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            outcome["status"] = "manual_review"
    if has_more and out:
        out[-1]["next_cursor"] = out[-1]["cursor"]
    return tuple(out)


def audit_derived_receipt_retention(
    service: Any, *, identity_id: str, after_cursor: str = "",
) -> dict[str, Any]:
    """Read-only, page-scoped inventory for future supervised consolidation.

    Do not treat "already_committed" as permission to unlink: the receipt,
    its SQL intent and exact derived OriginLink remain authority evidence.
    This method never performs filesystem or SQLite mutations.
    """
    entries = review_pending_derived_receipts(
        service, identity_id=identity_id, after_cursor=after_cursor,
        commit=False,
    )
    statuses = {
        "already_committed": 0,
        "recoverable": 0,
        "manual_review": 0,
        "approval_required": 0,
    }
    total_bytes = 0
    unknown_bytes = 0
    config = getattr(service, "config", None)
    raw_root = config.get("library.directory", None) if config is not None else None
    identity = service.storage.identity_get(identity_id)
    directory_name = identity.get("directory_name") if isinstance(identity, dict) else None
    if not isinstance(raw_root, str) or not isinstance(directory_name, str):
        raise ValueError("derived_recovery_invalid_scope")
    root = Path(os.path.expandvars(raw_root)).expanduser() / directory_name
    for entry in entries:
        status = entry["status"]
        statuses[status] = statuses.get(status, 0) + 1
        try:
            cursor = entry["cursor"]
            if (not isinstance(cursor, str) or not cursor
                    or Path(cursor).is_absolute() or ".." in Path(cursor).parts):
                raise ValueError("derived_recovery_cursor_invalid")
            receipt = root / cursor
            fd = _open_under_root_nofollow(root, receipt)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("derived_receipt_not_regular")
                total_bytes += info.st_size
            finally:
                os.close(fd)
        except (OSError, ValueError):
            unknown_bytes += 1
    next_cursor = entries[-1].get("next_cursor") if entries else None
    return {
        "identity_id": identity_id,
        "count": len(entries),
        "statuses": statuses,
        "receipt_bytes_observed": total_bytes,
        "unknown_size_count": unknown_bytes,
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
        "destructive_cleanup_allowed": False,
    }


def compact_committed_derived_intent(
    service: Any, *, identity_id: str, receipt_cursor: str,
    approved_receipt_sha256: str, after_cursor: str = "",
) -> dict[str, Any]:
    """Operator-approved SQL-only compaction of ONE finished intent.

    This never unlinks, moves, rewrites, or truncates filesystem evidence.
    The canonical Placement and derived OriginLink must be re-attested under
    the same BEGIN IMMEDIATE transaction as the metadata DELETE.
    """
    if (type(receipt_cursor) is not str or not receipt_cursor
            or len(receipt_cursor) > 512
            or type(approved_receipt_sha256) is not str
            or len(approved_receipt_sha256) != 64):
        raise ValueError("derived_compaction_invalid_coordinates")
    if (getattr(getattr(service, "current_identity", None), "identity_id", None)
            != identity_id):
        raise ValueError("derived_recovery_active_identity_mismatch")
    approved = [
        row for row in review_pending_derived_receipts(
            service, identity_id=identity_id, after_cursor=after_cursor,
            commit=False,
        )
        if row.get("cursor") == receipt_cursor
        and row.get("status") == "already_committed"
        and row.get("sha256") == approved_receipt_sha256
    ]
    if len(approved) != 1:
        raise ValueError("derived_compaction_unattested_receipt")
    identity = service.storage.identity_get(identity_id)
    config = getattr(service, "config", None)
    raw_root = config.get("library.directory", None) if config else None
    dirname = identity.get("directory_name") if isinstance(identity, dict) else None
    if not isinstance(raw_root, str) or not isinstance(dirname, str):
        raise ValueError("derived_recovery_invalid_scope")
    folder = Path(os.path.expandvars(raw_root)).expanduser() / dirname
    journal = folder / receipt_cursor
    raw = _read_receipt_nofollow(journal, profile_root=folder)
    if hashlib.sha256(raw).hexdigest() != approved_receipt_sha256:
        raise ValueError("derived_compaction_receipt_changed")
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("identity_id") != identity_id):
        raise ValueError("derived_compaction_invalid_receipt")
    target = str(data["published_path"])
    artifact_id = "sha256:" + data["output_sha256"]
    placement_id = service.storage._stable_id("placement", target)
    recipe_id = service.storage._stable_id(
        "variant", data["source_sha256"], data["transformer_id"],
        str(data["profile_id"] or ""), str(data["options_fingerprint"] or ""),
    )
    origin_proof = "derived:" + recipe_id + ":" + placement_id
    storage = service.storage
    key = _pending_intent_key(journal)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Reattest the same receipt bytes after acquiring the SQL writer
        # lock. An owner modifying JSON between preview and commit cannot
        # cause the original intent to be retired under stale approval.
        latest = _read_receipt_nofollow(journal, profile_root=folder)
        if hashlib.sha256(latest).hexdigest() != approved_receipt_sha256:
            raise ValueError("derived_compaction_receipt_changed")
        valid = db.execute(
            "SELECT 1 FROM placements p "
            "JOIN origin_links o ON o.artifact_id=p.artifact_id "
            "WHERE p.placement_id=? AND p.path=? AND p.identity_id=? "
            "AND p.artifact_id=? AND p.provider_id='cifereca' "
            "AND p.state='present' "
            "AND o.identity_id=? AND o.origin_kind='derived' "
            "AND o.parent_artifact_id=? AND o.evidence_ref=? LIMIT 1",
            (placement_id, target, identity_id, artifact_id,
             identity_id, data["source_artifact_id"], origin_proof),
        ).fetchone()
        if valid is None:
            raise ValueError("derived_compaction_canonical_proof_missing")
        intent = db.execute(
            "SELECT value FROM metadata WHERE key=?", (key,),
        ).fetchone()
        if intent is None:
            result = "already_compacted"
        elif intent[0] != approved_receipt_sha256:
            raise ValueError("derived_compaction_intent_changed")
        else:
            changed = db.execute(
                "DELETE FROM metadata WHERE key=? AND value=?",
                (key, approved_receipt_sha256),
            )
            if changed.rowcount != 1:
                raise ValueError("derived_compaction_intent_changed")
            result = "compacted"
    return {
        "status": result,
        "receipt_cursor": receipt_cursor,
        "receipt_retained": True,
        "canonical_origin_retained": True,
        "intent_removed": result == "compacted",
    }



def summarize_derived_receipt_retention(
    service: Any, *, identity_id: str, after_cursor: str = "",
    max_pages: int = 16,
) -> dict[str, Any]:
    """Bounded, read-only multi-page retention summary.

    Deliberately not an atomic snapshot or a cleanup authorization. The
    caller may resume through next_cursor if max_pages is reached; a changed
    filesystem can affect later pages, so no financial or forensic inference
    may assume a frozen inventory.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 64:
        raise ValueError("derived_retention_invalid_page_limit")
    if (type(after_cursor) is not str or len(after_cursor) > 512
            or chr(0) in after_cursor):
        raise ValueError("derived_recovery_cursor_invalid")
    statuses = {
        "already_committed": 0, "recoverable": 0,
        "manual_review": 0, "approval_required": 0,
    }
    cursor = after_cursor
    total_count = 0
    receipt_bytes = 0
    unknown_sizes = 0
    pages = 0
    has_more = False
    while pages < max_pages:
        page = audit_derived_receipt_retention(
            service, identity_id=identity_id, after_cursor=cursor,
        )
        pages += 1
        total_count += page["count"]
        receipt_bytes += page["receipt_bytes_observed"]
        unknown_sizes += page["unknown_size_count"]
        for key, value in page["statuses"].items():
            statuses[key] = statuses.get(key, 0) + value
        next_cursor = page["next_cursor"]
        has_more = page["has_more"]
        if not has_more:
            cursor = ""
            break
        if (not isinstance(next_cursor, str)
                or next_cursor <= cursor):
            raise ValueError("derived_retention_nonprogressing_cursor")
        cursor = next_cursor
    return {
        "identity_id": identity_id,
        "pages_scanned": pages,
        "receipt_count_observed": total_count,
        "statuses": statuses,
        "receipt_bytes_observed": receipt_bytes,
        "unknown_size_count": unknown_sizes,
        "complete": not has_more,
        "next_cursor": cursor if has_more else None,
        "destructive_cleanup_allowed": False,
    }
