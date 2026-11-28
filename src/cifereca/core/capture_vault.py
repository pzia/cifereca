"""Shared encrypted capture storage engine, extracted from Browser Recorder.

Internal engine: callers must supply a validated codec. Scope, key ownership
and public access are assigned by the Core service, not by the storage codec.
This extraction alone does not provide that service or its authorization.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone, timedelta
import fcntl
import unicodedata
import os
from pathlib import Path
import re
import secrets
import stat
from time import monotonic

MAGIC = b"CFRVLT01"
LABEL_MAGIC = b"CFRLBL01"
MAX_LABEL_FILE = 1024
MAX_FILE = 3 * 1024 * 1024
MAX_SESSIONS = 128
MAX_VAULT_BYTES = 128 * 1024 * 1024
_ID = re.compile(r"^[0-9a-f]{32}$")
_STAGE = re.compile(r"^\.[0-9a-f]{32}-[0-9a-f]{16}\.(?:part|labelpart)$")
MIN_STAGING_AGE_SECONDS = 24 * 60 * 60
_STATES = frozenset(("recording", "completed", "interrupted"))

class VaultError(Exception):
    """Only closed, non-private error codes; never interpolate source material."""

    def __init__(self, code="vault_failed"):
        super().__init__(code)


def _key(master_key: bytes, salt: bytes) -> bytes:
    """Derive a per-record key from the installation's random 256-bit key."""
    if type(master_key) is not bytes or len(master_key) != 32:
        raise VaultError("vault_key_unavailable")
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                    info=b"cifereca.capture-vault/v1").derive(master_key)
    except ImportError as exc:
        raise VaultError("vault_crypto_unavailable") from exc


def _aead():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as exc:
        raise VaultError("vault_crypto_unavailable") from exc


class EncryptedCaptureVault:
    """Directory trusted by Core, but its final component never follows links."""

    def __init__(self, directory: Path):
        self.path = Path(directory)

    # A consumer codec owns the record schema and its in-memory lifetime.
    # No provider or Browser Recorder import is allowed in the storage engine.
    def _encode(self, capture, session_id: str, state: str) -> bytes:
        raise NotImplementedError

    def _decode(self, data: bytes, session_id: str):
        raise NotImplementedError

    def _release(self, stored) -> None:
        raise NotImplementedError

    def _mark_sealed(self, capture, session_id: str) -> None:
        raise NotImplementedError

    @property
    def max_file(self):
        return MAX_FILE

    @property
    def max_vault_bytes(self):
        return MAX_VAULT_BYTES

    def _open_dir(self, *, create: bool):
        try:
            if create:
                self.path.mkdir(mode=0o700, parents=False, exist_ok=True)
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags)
            info = os.fstat(fd)
            if (not stat.S_ISDIR(info.st_mode) or
                    info.st_uid != os.getuid() or
                    stat.S_IMODE(info.st_mode) != 0o700):
                os.close(fd)
                raise VaultError("vault_unsafe_directory")
            return fd
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_unsafe_directory") from exc

    def _staging(self, fd):
        """Recognize only our opaque write staging files, without following links."""
        found = []
        for name in os.listdir(fd):
            if _STAGE.fullmatch(name):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or not 0 <= info.st_size <= self.max_file):
                    raise VaultError("vault_unsafe_file")
                found.append((name, info))
        return tuple(found)

    def _reserved_ids(self, fd):
        """Storage codec may reserve opaque IDs under the directory lock."""
        return frozenset()

    def _entries(self, fd):
        """Count encrypted archives, labels AND orphan writes toward the quota."""
        total = count = 0
        try:
            staging = dict(self._staging(fd))
            for filename in os.listdir(fd):
                if filename in staging:
                    info = staging[filename]
                elif (filename.endswith(".cbrv") or filename.endswith(".cbrn")):
                    if not _ID.fullmatch(filename[:-5]):
                        continue
                    info = os.stat(filename, dir_fd=fd, follow_symlinks=False)
                    max_size = self.max_file if filename.endswith(".cbrv") else MAX_LABEL_FILE
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600
                            or not 0 <= info.st_size <= max_size):
                        raise VaultError("vault_unsafe_file")
                    if filename.endswith(".cbrv"):
                        count += 1
                else:
                    continue
                total += info.st_size
            return count, total
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc

    def list_ids(self) -> tuple[str, ...]:
        """Opaque random IDs only; never leak site, book or account metadata."""
        if not self.path.exists() and not self.path.is_symlink():
            return ()
        fd = self._open_dir(create=False)
        try:
            ids = []
            for name in os.listdir(fd):
                if name.endswith(".cbrv") and _ID.fullmatch(name[:-5]):
                    ids.append(name[:-5])
                    if len(ids) > MAX_SESSIONS:
                        raise VaultError("vault_quota_exceeded")
            return tuple(sorted(ids))
        finally:
            os.close(fd)

    @staticmethod
    def _label_value(value: str) -> str:
        # No newline, control, bidi format or terminal escape in GTK labels.
        # Label is private metadata, never a filename or diagnostic.
        if (type(value) is not str or not 1 <= len(value) <= 80
                or len(value.encode("utf-8")) > 320
                or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"}
                       for char in value)):
            raise VaultError("vault_label_invalid")
        return value

    def get_label(self, session_id: str, master_key: bytes) -> str:
        """Read AEAD sidecar after authenticating the immutable capture."""
        session = self.open(session_id, master_key)
        self._release(session)
        fd = self._open_dir(create=False)
        try:
            try:
                handle = os.open(
                    session_id + ".cbrn",
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK, dir_fd=fd,
                )
            except FileNotFoundError:
                return ""
            try:
                info = os.fstat(handle)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or not 53 <= info.st_size <= MAX_LABEL_FILE):
                    raise VaultError("vault_unsafe_file")
                with os.fdopen(handle, "rb", closefd=False) as stream:
                    data = stream.read(MAX_LABEL_FILE + 1)
            finally:
                os.close(handle)
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc
        finally:
            os.close(fd)
        if len(data) > MAX_LABEL_FILE or not data.startswith(LABEL_MAGIC):
            raise VaultError("vault_label_invalid")
        salt = data[8:24]
        nonce = data[24:36]
        try:
            plaintext = _aead()(_key(master_key, salt)).decrypt(
                nonce, data[36:], LABEL_MAGIC + salt + nonce
                + bytes.fromhex(session_id),
            )
            return self._label_value(plaintext.decode("utf-8"))
        except Exception as exc:
            raise VaultError("vault_label_invalid") from exc

    def set_label(self, session_id: str, master_key: bytes, label: str, *, guard=None) -> None:
        """Atomic, separately encrypted metadata; never change .cbrv."""
        label = self._label_value(label)
        # Authentication must occur *under* the same directory lock as
        # replacing the sidecar. Otherwise a concurrent delete/retention
        # could remove the archive after the check and this method would
        # resurrect an orphan encrypted name for a deleted session.
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        encrypted = _aead()(_key(master_key, salt)).encrypt(
            nonce, label.encode("utf-8"),
            LABEL_MAGIC + salt + nonce + bytes.fromhex(session_id),
        )
        data = LABEL_MAGIC + salt + nonce + encrypted
        if len(data) > MAX_LABEL_FILE:
            raise VaultError("vault_label_invalid")
        fd = self._open_dir(create=False)
        temp = "." + session_id + "-" + secrets.token_hex(8) + ".labelpart"
        dest = session_id + ".cbrn"
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            # Authenticate source and any prior name while holding the
            # exclusive lock, before checking quotas or allocating staging.
            # Reject missing/altered archives and forged label sidecars.
            self.get_label(session_id, master_key)
            if guard is not None:
                guard(fd)
            try:
                old = os.stat(dest, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                old = None
            if old is not None and (
                not stat.S_ISREG(old.st_mode) or old.st_nlink != 1
                or old.st_uid != os.getuid()
                or stat.S_IMODE(old.st_mode) != 0o600
            ):
                raise VaultError("vault_unsafe_file")
            _count, total = self._entries(fd)
            previous_size = old.st_size if old is not None else 0
            if total - previous_size + len(data) > self.max_vault_bytes:
                raise VaultError("vault_limit")
            handle = os.open(
                temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=fd,
            )
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, dest, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temp, dir_fd=fd)
                except FileNotFoundError:
                    pass
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_write_failed") from exc
        finally:
            os.close(fd)

    @staticmethod
    def _retention_date(raw: str) -> datetime:
        try:
            value = datetime.fromisoformat(raw)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("naive timestamp")
            return value.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError) as exc:
            raise VaultError("vault_retention_invalid") from exc

    @staticmethod
    def _retention_bytes(fd, name: str, max_bytes: int) -> bytes:
        """Only the exact opaque filename under a trusted directory FD."""
        try:
            handle = os.open(
                name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK, dir_fd=fd,
            )
            try:
                info = os.fstat(handle)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or not (53 if max_bytes == MAX_LABEL_FILE else 64)
                        <= info.st_size <= max_bytes):
                    raise VaultError("vault_unsafe_file")
                with os.fdopen(handle, "rb", closefd=False) as stream:
                    data = stream.read(max_bytes + 1)
                if len(data) != info.st_size:
                    raise VaultError("vault_unsafe_file")
                return data
            finally:
                os.close(handle)
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc

    def _retention_fingerprint(self, fd, sid: str):
        if type(sid) is not str or not _ID.fullmatch(sid):
            raise VaultError("vault_invalid")
        capture = self._retention_bytes(fd, sid + ".cbrv", self.max_file)
        label_name = sid + ".cbrn"
        try:
            label = self._retention_bytes(fd, label_name, MAX_LABEL_FILE)
        except VaultError as exc:
            # Missing labels are fine; malformed/unsafe links are not.
            if not isinstance(exc.__cause__, FileNotFoundError):
                raise
            label = None
        return ((sid, sha256(capture).hexdigest(),
                 sha256(label).hexdigest() if label is not None else None),
                len(capture) + (len(label) if label is not None else 0))

    @staticmethod
    def _unlink_capture(fd, sid, has_label):
        if has_label:
            os.unlink(sid + ".cbrn", dir_fd=fd)
        os.unlink(sid + ".cbrv", dir_fd=fd)

    def auto_prune(self, key, *, older_than_days, pinned, reserve_bytes=0,
                   reserve_session=False, now=None):
        """Apply the configured age/volume policy to authenticated snapshots.

        This is a Core-only operation. All candidates and sidecars are checked
        under the existing writer lock before deletion. Active/recent recording
        snapshots are never volume-evicted; stale ones age out. Pinned sessions
        may temporarily block admission rather than exceed the hard caps.
        """
        if (type(older_than_days) is not int or not 1 <= older_than_days <= 365
                or type(reserve_bytes) is not int or not 0 <= reserve_bytes <= self.max_file):
            raise VaultError("vault_retention_invalid")
        if not self.path.exists() and not self.path.is_symlink():
            return 0, 0
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=older_than_days)
        fd = self._open_dir(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            count, total = self._entries(fd)
            rows = []
            reserved = self._reserved_ids(fd)
            for sid in self.list_ids():
                if sid in reserved:
                    continue
                stored = self.open(sid, key)
                try:
                    fingerprint, size = self._retention_fingerprint(fd, sid)
                    label = self.get_label(sid, key) if fingerprint[2] is not None else ""
                    date = self._retention_date(stored.saved_at)
                    rows.append((date, sid, fingerprint[2] is not None, size,
                                 stored.recovery_status, pinned(label, now)))
                finally:
                    self._release(stored)
            # Old orphan staging is ciphertext and the lock excludes live writes.
            stale = [(name, info) for name, info in self._staging(fd)
                     if now.timestamp() - info.st_mtime >= MIN_STAGING_AGE_SECONDS]
            total -= sum(info.st_size for _, info in stale)
            selected = []
            for date, sid, label, size, state, keep in sorted(rows):
                if not keep and date < cutoff:
                    selected.append((sid, label, size))
                    total -= size
                    count -= 1
            chosen = {sid for sid, _, _ in selected}
            for date, sid, label, size, state, keep in sorted(rows):
                if (total + reserve_bytes <= self.max_vault_bytes
                        and count + int(reserve_session) <= MAX_SESSIONS):
                    break
                if sid in chosen or keep or state == "partial":
                    continue
                selected.append((sid, label, size))
                total -= size
                count -= 1
            if reserve_session and (total + reserve_bytes > self.max_vault_bytes
                                    or count + 1 > MAX_SESSIONS):
                # If pinned/recent partial captures block admission, do not
                # delete a prefix of otherwise valid sessions for no benefit.
                raise VaultError("vault_limit")
            for name, _ in stale:
                os.unlink(name, dir_fd=fd)
            for sid, label, _ in selected:
                self._unlink_capture(fd, sid, label)
            if stale or selected:
                os.fsync(fd)
            return len(selected), sum(size for _, _, size in selected)
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc
        finally:
            os.close(fd)

    def begin(self, master_key: bytes):
        """Explicit action *before* capture; fail closed before Chromium runs."""
        salt = secrets.token_bytes(16)
        key = _key(master_key, salt)
        _aead()
        fd = self._open_dir(create=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            count, total = self._entries(fd)
            if count >= MAX_SESSIONS or total >= self.max_vault_bytes:
                raise VaultError("vault_limit")
            disk = os.fstatvfs(fd)
            if disk.f_bavail * disk.f_frsize < 8 * 1024 * 1024:
                raise VaultError("vault_limit")
            return SessionWriter(self, secrets.token_hex(16), salt, key)
        finally:
            os.close(fd)

    def open(self, session_id: str, master_key: bytes) :
        if type(session_id) is not str or not _ID.fullmatch(session_id):
            raise VaultError("vault_invalid")
        fd = self._open_dir(create=False)
        try:
            name = session_id + ".cbrv"
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK
            file_fd = os.open(name, flags, dir_fd=fd)
            try:
                info = os.fstat(file_fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or not 64 <= info.st_size <= self.max_file):
                    raise VaultError("vault_unsafe_file")
                with os.fdopen(file_fd, "rb", closefd=False) as stream:
                    data = stream.read(self.max_file + 1)
            finally:
                os.close(file_fd)
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc
        finally:
            os.close(fd)
        if not data.startswith(MAGIC) or len(data) > self.max_file:
            raise VaultError("vault_invalid")
        salt = data[len(MAGIC):len(MAGIC) + 16]
        nonce = data[len(MAGIC) + 16:len(MAGIC) + 28]
        ciphertext = data[len(MAGIC) + 28:]
        key = _key(master_key, salt)
        try:
            raw = _aead()(key).decrypt(
                nonce, ciphertext, MAGIC + salt + nonce + bytes.fromhex(session_id),
            )
        except Exception as exc:
            raise VaultError("vault_unlock_failed") from exc
        return self._decode(raw, session_id)

    def delete(self, session_id: str, master_key: bytes, *, confirmed=False):
        if confirmed is not True:
            raise VaultError("vault_confirmation_required")
        fd = self._open_dir(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            session = self.open(session_id, master_key)
            self._release(session)
            try:
                sidecar = os.stat(
                    session_id + ".cbrn", dir_fd=fd, follow_symlinks=False,
                )
            except FileNotFoundError:
                sidecar = None
            if sidecar is not None and (
                not stat.S_ISREG(sidecar.st_mode) or sidecar.st_nlink != 1
                or sidecar.st_uid != os.getuid()
                or stat.S_IMODE(sidecar.st_mode) != 0o600
            ):
                raise VaultError("vault_unsafe_file")
            self._unlink_capture(fd, session_id, sidecar is not None)
            os.fsync(fd)
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc
        finally:
            os.close(fd)


class SessionWriter:
    """Immutable random session ID, atomic authenticated snapshots only."""

    def __init__(self, vault, session_id: str, salt: bytes, key: bytes):
        self.vault, self.session_id = vault, session_id
        self._salt, self._key = salt, key
        self._last = 0.0
        self._sealed = False

    def save(self, trace, state="recording", *, force=False):
        if self._sealed:
            raise VaultError("vault_sealed")
        if state not in _STATES:
            raise VaultError("vault_invalid")
        if (not force and state == "recording"
                and monotonic() - self._last < 1.0):
            return
        plaintext = self.vault._encode(trace, self.session_id, state)
        nonce = secrets.token_bytes(12)
        ciphertext = _aead()(self._key).encrypt(
            nonce, plaintext, MAGIC + self._salt + nonce
            + bytes.fromhex(self.session_id),
        )
        data = MAGIC + self._salt + nonce + ciphertext
        if len(data) > self.vault.max_file:
            raise VaultError("vault_limit")
        fd = self.vault._open_dir(create=False)
        tmp = "." + self.session_id + "-" + secrets.token_hex(8) + ".part"
        dest = self.session_id + ".cbrv"
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            count, total = self.vault._entries(fd)
            try:
                old = os.stat(dest, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                old = None
            if old is not None and (
                not stat.S_ISREG(old.st_mode) or old.st_nlink != 1
                or old.st_uid != os.getuid()
                or stat.S_IMODE(old.st_mode) != 0o600
            ):
                raise VaultError("vault_unsafe_file")
            # Replacing an existing snapshot does not allocate a *second*
            # permanent session. Account for its previously persisted size
            # before checking the final cap (temporary fsync space is managed
            # by the filesystem; never loosen the total retention quota).
            if old is None and self._last:
                # A purged or explicitly deleted snapshot must not reappear.
                raise VaultError("vault_sealed")
            previous_size = old.st_size if old is not None else 0
            new_slot = old is None and self.session_id not in self.vault._reserved_ids(fd)
            if (total - previous_size + len(data) > self.vault.max_vault_bytes
                    or count + int(new_slot) > MAX_SESSIONS):
                raise VaultError("vault_limit")
            handle = os.open(
                tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=fd,
            )
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, dest, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(tmp, dir_fd=fd)
                except FileNotFoundError:
                    pass
            self._last = monotonic()
            if state != "recording":
                self._sealed = True
                self.vault._mark_sealed(trace, self.session_id)
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_write_failed") from exc
        finally:
            os.close(fd)
