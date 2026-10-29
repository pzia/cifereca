"""Completed local-copy checkpoints, never rights or presence authority.

Only a new explicit send under the acquisition lock may recover these bytes.
The checkpoint precedes final-name publication; canonical_transfer still owns
the Placement/OriginLink transaction and all physical/source revalidation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import time

from ..locking import InterProcessLock
from .canonical_publication import _physical_target, _same_file, _stable

_SCHEMA = "local.completed-copy.v1"
_READY = "local_transfer_staged"
_PUBLISHED = "local_transfer_published"


def sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return  # Python cannot open directory handles for fsync on Windows.
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class LocalTransferCheckpoint:
    def __init__(self, storage, *, root, source, recipient, source_path,
                 destination_dir, artifact_id, sha256, shared_lock):
        self.storage, self.root = storage, root
        self.source_path, self.destination_dir = source_path, destination_dir
        self.sha256, self.lock = sha256, shared_lock
        self.scope = {
            "root": str(root), "from_identity_id": source["identity_id"],
            "to_identity_id": recipient, "from_placement_id": source["placement_id"],
            "source_path": str(source_path), "artifact_id": artifact_id,
            "sha256": sha256, "destination_dir": str(destination_dir),
        }
        self.record_id = _stable("local-transfer", json.dumps(self.scope, sort_keys=True))
        self.coords = ("cifereca", "local", recipient, source["placement_id"], recipient)

    def _guard(self, db):
        from ..paths import acquisition_lock_file
        if (type(self.lock) is not InterProcessLock or self.lock._handle is None
                or self.lock.path.resolve() != acquisition_lock_file().resolve()):
            raise ValueError("local_transfer_lock_required")
        source = db.execute(
            "SELECT identity_id,path,artifact_id,state FROM placements WHERE placement_id=?",
            (self.scope["from_placement_id"],),
        ).fetchone()
        if source != (self.scope["from_identity_id"], str(self.source_path),
                      self.scope["artifact_id"], "present"):
            raise ValueError("local_transfer_source_changed")
        source_stat = os.stat(self.source_path, follow_symlinks=False)
        artifact = db.execute("SELECT sha256,size FROM artifacts WHERE artifact_id=?",
                              (self.scope["artifact_id"],)).fetchone()
        if (not stat.S_ISREG(source_stat.st_mode)
                or artifact != (self.sha256, source_stat.st_size)):
            raise ValueError("local_transfer_source_changed")
        directories = {}
        for key in ("from_identity_id", "to_identity_id"):
            rows = db.execute("SELECT directory_name FROM identities WHERE identity_id=?",
                              (self.scope[key],)).fetchall()
            if len(rows) != 1 or type(rows[0][0]) is not str:
                raise ValueError("local_transfer_profile_changed")
            directories[key] = rows[0][0]
        if directories["from_identity_id"] == directories["to_identity_id"]:
            raise ValueError("local_transfer_profile_changed")
        _physical_target(self.source_path, self.root, directories["from_identity_id"])
        if _physical_target(self.destination_dir, self.root,
                            directories["to_identity_id"]) != self.destination_dir:
            raise ValueError("local_transfer_profile_changed")

    def _read(self, db):
        self._guard(db)
        row = db.execute(
            "SELECT provider_id,credential_id,account_id,item_id,identity_id,status,"
            "metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (self.record_id,),
        ).fetchone()
        if row is None:
            return None
        if row[:5] != self.coords or row[5] not in (_READY, _PUBLISHED):
            raise ValueError("local_transfer_journal_conflict")
        data = json.loads(row[6])
        if (type(data) is not dict or data.get("schema") != _SCHEMA
                or data.get("scope") != self.scope):
            raise ValueError("local_transfer_journal_conflict")
        for field in ("temporary", "destination"):
            if type(data.get(field)) is not str:
                raise ValueError("local_transfer_journal_conflict")
            path = Path(data[field])
            if (not path.is_absolute() or ".." in path.parts
                    or path.parent != self.destination_dir):
                raise ValueError("local_transfer_journal_conflict")
        if not Path(data["temporary"]).name.startswith(".cifereca-send-"):
            raise ValueError("local_transfer_journal_conflict")
        pin = data.get("pin")
        if (type(pin) is not list or len(pin) != 4
                or any(type(n) is not int or n < 0 for n in pin)):
            raise ValueError("local_transfer_journal_conflict")
        data["_status"] = row[5]
        return data

    def exists(self):
        """A stale checkpoint must not downgrade into a legacy copy path."""
        with self.storage._lock, self.storage._connect() as db:
            return db.execute("SELECT 1 FROM acquisition_ledger WHERE acquisition_id=?",
                              (self.record_id,)).fetchone() is not None

    def _pin(self, path):
        if (path.parent != self.destination_dir or path.is_symlink()
                or path.resolve(strict=True) != path):
            raise ValueError("local_transfer_copy_changed")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
                raise ValueError("local_transfer_copy_changed")
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != self.sha256 or not _same_file(path, before):
            raise ValueError("local_transfer_copy_changed")
        return [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns]

    def record_ready(self, temporary: Path, destination: Path):
        """After fsync/hash, before exposing final name. No B state is published."""
        pin = self._pin(temporary)
        source_stat = self.source_path.stat()
        if pin[:2] == [source_stat.st_dev, source_stat.st_ino]:
            raise ValueError("local_transfer_independent_copy_required")
        if destination.parent != self.destination_dir or destination.is_symlink():
            raise ValueError("local_transfer_copy_changed")
        data = dict(schema=_SCHEMA, scope=self.scope, temporary=str(temporary),
                    destination=str(destination), pin=pin)
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
        now = int(time.time())
        with self.storage._lock, self.storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._read(db)
            if previous is None:
                db.execute(
                    "INSERT INTO acquisition_ledger(acquisition_id,provider_id,credential_id,"
                    "account_id,item_id,identity_id,status,strategy,executor,metadata_json,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.record_id, *self.coords, _READY, "local_transfer", "core",
                     encoded, now, now),
                )
            else:
                # A fresh explicit send may replace only a lost checkpoint.
                # Never supersede a still recoverable or conflicting inode.
                if (previous["_status"] != _PUBLISHED and
                        any(os.path.lexists(previous[k]) for k in ("temporary", "destination"))):
                    raise ValueError("local_transfer_journal_conflict")
                db.execute("UPDATE acquisition_ledger SET status=?,metadata_json=?,"
                           "updated_at=? WHERE acquisition_id=?",
                           (_READY, encoded, now, self.record_id))
        sync_directory(self.destination_dir)

    def recover(self):
        with self.storage._lock, self.storage._connect() as db:
            data = self._read(db)
        if data is None:
            return None
        temporary, destination = Path(data["temporary"]), Path(data["destination"])
        if os.path.lexists(destination):
            try:
                same_copy = self._pin(destination) == data["pin"]
            except (ValueError, OSError):
                same_copy = False
            if not same_copy and data["_status"] == _PUBLISHED:
                # B may have changed its file after a completed transfer.
                # A new explicit send creates another independent copy;
                # it never overwrites/re-adopts B's current bytes.
                return None
            if not same_copy:
                raise ValueError("local_transfer_copy_changed")
            return destination
        if not os.path.lexists(temporary):
            return None
        if self._pin(temporary) != data["pin"]:
            raise ValueError("local_transfer_copy_changed")
        os.link(temporary, destination)  # Exclusive; never replace a B-owned file.
        sync_directory(self.destination_dir)
        if self._pin(destination) != data["pin"]:
            raise ValueError("local_transfer_copy_changed")
        return destination

    def finish(self, destination):
        """Only after canonical publication. This status is not a receipt/right."""
        with self.storage._lock, self.storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            data = self._read(db)
            if data is None or data["destination"] != str(destination):
                return
            if self._pin(destination) != data["pin"]:
                raise ValueError("local_transfer_copy_changed")
            db.execute("UPDATE acquisition_ledger SET status=?,updated_at=? "
                       "WHERE acquisition_id=?", (_PUBLISHED, int(time.time()), self.record_id))
        temporary = Path(data["temporary"])
        if os.path.lexists(temporary):
            if self._pin(temporary) != data["pin"]:
                raise ValueError("local_transfer_copy_changed")
            temporary.unlink()
            sync_directory(self.destination_dir)
