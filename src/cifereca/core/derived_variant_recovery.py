"""#4520: durable, identity-scoped receipts for FS-before-SQL derivative recovery.

No untrusted pathname, content SHA or global variant recipe alone is authority.
Recovery is explicit (never on startup), bounded and non-destructive.
"""
from __future__ import annotations

import hashlib
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


def _stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        s = path.lstat()
        if stat.S_ISREG(s.st_mode):
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    except OSError:
        pass
    return None


def _verified_sha(path: Path, expected_stamp: tuple[int, ...],
                  expected_sha: str) -> bool:
    """Hash exactly the O_NOFOLLOW inode, checking fstat before/after read."""
    if (_stamp(path) != expected_stamp or type(expected_sha) is not str
            or len(expected_sha) != 64):
        return False
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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


def _read_receipt_nofollow(journal: Path) -> bytes:
    """Read a bounded, stable regular file, never an attacker-controlled link."""
    expected = _stamp(journal)
    if expected is None or expected[2] > MAX_BYTES:
        raise ValueError("derived_receipt_invalid_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(journal, flags)
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


def _safe_unlink_receipt(path: Path) -> None:
    """Remove only a receipt owned by this operation, never a foreign inode."""
    if not _directory(path.parent) or _stamp(path) is None:
        return
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.unlink(path.name, dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)


def write_pending_derived_receipt(staged: Path, published: Path, *,
                                  stage_dir_fd: int,
                                  source_path: Path, source_artifact_id: str,
                                  source_sha256: str, source_placement_id: str,
                                  identity_id: str, output_sha256: str,
                                  output_size: int, media_format: str,
                                  mime_type: str | None, variant_kind: str,
                                  transformer_id: str, transformer_version: str,
                                  profile_id: str | None, profile_version: str,
                                  options_fingerprint: str, book_id: str | None,
                                  warnings: tuple[str, ...] | list[str],
                                  facts: dict[str, Any]) -> Path:
    """fsync output, receipt, and directory BEFORE exclusive hardlink creation."""
    if (_stamp(staged) is None or not _directory(staged.parent)
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
    journal = staged.parent / ("pending-" + secrets.token_hex(16) + ".json")
    fd = os.open(journal.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=stage_dir_fd)
    try:
        with os.fdopen(fd, "wb", closefd=True) as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
    except Exception:
        _safe_unlink_receipt(journal)
        raise
    os.fsync(stage_dir_fd)
    return journal


_INTENT_KEY = "derived.publication.pending.v1:"


def _pending_intent_key(journal: Path) -> str:
    name = journal.name
    nonce = name.removeprefix("pending-").removesuffix(".json")
    if (not name.startswith("pending-") or not name.endswith(".json")
            or len(nonce) != 32
            or any(ch not in "0123456789abcdef" for ch in nonce)):
        raise ValueError("derived_receipt_name_invalid")
    return _INTENT_KEY + nonce


def register_pending_derived_receipt(storage: Any, journal: Path) -> None:
    """Create a durable DB intent before hardlink; not an OriginLink or right."""
    raw = _read_receipt_nofollow(journal)
    digest = hashlib.sha256(raw).hexdigest()
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


def retire_pending_derived_receipt(storage: Any, journal: Path) -> None:
    """After SQL success, remove only the receipt then its DB intent."""
    _safe_unlink_receipt(journal)
    if journal.exists() or journal.is_symlink():
        return
    key = _pending_intent_key(journal)
    with storage._lock, storage._connect() as db:
        db.execute("DELETE FROM metadata WHERE key=?", (key,))


def review_pending_derived_receipts(service: Any, *, identity_id: str,
                                    commit: bool = False,
                                    approved_receipt_sha256: str = "") -> tuple[dict[str, str], ...]:
    """Explicit bounded review/recovery. No ambiguous evidence is promoted.

    The journal is NOT a provider right. For commit, an operator must approve
    the SHA256 of the exact reviewed receipt bytes; no unattended bulk replay.
    """
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
    receipts: list[Path] = []
    for subdir in [folder, *(p for p in sorted(folder.iterdir()) if _directory(p) and not p.is_symlink())]:
        journal_dir = subdir / ".cifereca-staging" / "variants"
        if not _directory(journal_dir) or not _directory(journal_dir.parent):
            continue
        if journal_dir.is_symlink() or journal_dir.parent.is_symlink():
            continue
        for file in sorted(journal_dir.iterdir()):
            if file.name.startswith("pending-") and file.name.endswith(".json"):
                receipts.append(file)
                if len(receipts) > MAX_RECEIPTS:
                    raise ValueError("derived_recovery_receipt_limit")
    out: list[dict[str, str]] = []
    for journal in receipts:
        outcome = {"receipt": journal.name, "status": "manual_review"}
        out.append(outcome)
        try:
            receipt_bytes = _read_receipt_nofollow(journal)
            receipt_digest = hashlib.sha256(receipt_bytes).hexdigest()
            data = json.loads(receipt_bytes)
            if (type(data) is not dict or data.get("schema") != SCHEMA
                    or data.get("identity_id") != identity_id
                    or not _intent_matches(storage, journal, receipt_digest)):
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
            output_stamp = tuple(data["output_stamp"])
            if (type(data.get("output_size")) is not int
                    or data["output_size"] <= 0
                    or data["output_size"] != output_stamp[2]):
                continue
            if (source_stamp is None
                    or not _verified_sha(target, output_stamp, data["output_sha256"])
                    or not _verified_sha(source, source_stamp, data["source_sha256"])):
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
                        outcome["status"] = "already_committed"
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
            if (not _verified_sha(target, output_stamp, data["output_sha256"])
                    or not _verified_sha(source, source_stamp, data["source_sha256"])):
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
            )
            outcome["status"] = "recovered"
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            outcome["status"] = "manual_review"
    return tuple(out)
