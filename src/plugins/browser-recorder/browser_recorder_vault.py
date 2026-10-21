"""Opt-in, authenticated local vault for *observed* private HTTP traces.

Not a HAR, ZIP export, browser profile, or complete wire capture. Private URLs,
queries, redirects and captured text are encrypted before the first file write.
No operations occur at import time or while simply opening the plugin page.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
from time import monotonic

from browser_recorder_http import (
    MAX_BODY_BYTES, MAX_EXCHANGES, MAX_TOTAL_BODY_BYTES,
    PrivateExchange, PrivateHttpTrace,
)

SCHEMA = "cifereca.browser-recorder.private-session/1"
MAGIC = b"CFRVLT01"
MAX_FILE = 3 * 1024 * 1024
MAX_SESSIONS = 128
MAX_VAULT_BYTES = 128 * 1024 * 1024
_ID = re.compile(r"^[0-9a-f]{32}$")
_STATES = frozenset(("recording", "completed", "interrupted"))
_FIELD_NAMES = frozenset(field.name for field in fields(PrivateExchange))


class VaultError(Exception):
    """Only closed, non-private error codes; never interpolate source material."""

    def __init__(self, code="vault_failed"):
        super().__init__(code)


def _key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 1024:
        raise VaultError("vault_passphrase_required")
    try:
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
            passphrase.encode("utf-8")
        )
    except ImportError as exc:
        raise VaultError("vault_crypto_unavailable") from exc
    except (ValueError, UnicodeError) as exc:
        raise VaultError("vault_passphrase_required") from exc


def _aead():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as exc:
        raise VaultError("vault_crypto_unavailable") from exc


def _record(trace: PrivateHttpTrace, session_id: str, state: str) -> bytes:
    if state not in _STATES or not _ID.fullmatch(session_id):
        raise VaultError("vault_invalid")
    if len(trace.exchanges) > MAX_EXCHANGES:
        raise VaultError("vault_limit")
    entries = [asdict(e) for e in trace.exchanges]
    data = {
        "schema": SCHEMA,
        "id": session_id,
        "state": state,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "end_reason": trace.end_reason if (
            state == "completed" and trace.end_reason in (
                "button", "browser_closed",
            )
        ) else None,
        "origins": list(trace.origins),
        "metadata_only_origins": list(trace.metadata_only_origins),
        "limit": trace.limit,
        "max_body": trace.max_body,
        "total_body_remaining": trace.total_body,
        "omitted": dict(trace.omitted),
        "exchanges": entries,
    }
    try:
        data_bytes = json.dumps(
            data, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise VaultError("vault_invalid") from exc
    if len(data_bytes) > MAX_FILE - 128:
        raise VaultError("vault_limit")
    return data_bytes


def _unrecord(blob: bytes, session_id: str):
    try:
        data = json.loads(blob)
        if (type(data) is not dict or data["schema"] != SCHEMA
                or data["id"] != session_id or data["state"] not in _STATES):
            raise ValueError("invalid schema")
        origins, third = data["origins"], data["metadata_only_origins"]
        if (type(origins) is not list or type(third) is not list
                or not origins or len(origins) > 6 or len(third) > 4):
            raise ValueError("invalid origins")
        trace = PrivateHttpTrace(
            tuple(origins), metadata_only_origins=tuple(third),
            limit=data["limit"], max_body=data["max_body"],
            total_body=data["total_body_remaining"],
        )
        rows = data["exchanges"]
        if type(rows) is not list or len(rows) > MAX_EXCHANGES:
            raise ValueError("invalid entries")
        for index, row in enumerate(rows):
            if type(row) is not dict or set(row) != _FIELD_NAMES:
                raise ValueError("invalid entry")
            if (type(row["sequence"]) is not int or row["sequence"] != index + 1
                    or type(row["url"]) is not str or len(row["url"]) > 8192
                    or type(row["method"]) is not str
                    or row["method"] not in {
                        "GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH",
                        "DELETE", "OTHER",
                    }
                    or any(type(row[key]) is not int or row[key] < 0 for key in (
                        "status", "started_ms", "duration_ms", "page_id",
                    ))
                    or row["status"] > 599
                    or type(row["metadata_only"]) is not bool
                    or type(row["network_error"]) is not bool):
                raise ValueError("invalid entry values")
            for key in ("request_body", "response_body", "redirect_to"):
                if row[key] is not None and (
                    type(row[key]) is not str or len(row[key]) > MAX_BODY_BYTES * 2
                ):
                    raise ValueError("invalid body")
            for key in ("request_headers", "response_headers"):
                if type(row[key]) is not dict or len(row[key]) > 25 or any(
                    type(k) is not str or type(v) is not str
                    or len(k) > 80 or len(v) > 256 for k, v in row[key].items()
                ):
                    raise ValueError("invalid headers")
            trace.exchanges.append(PrivateExchange(**row))
        omitted = data["omitted"]
        if type(omitted) is not dict or any(
            k not in trace.omitted or type(v) is not int or v < 0
            for k, v in omitted.items()
        ):
            raise ValueError("invalid omissions")
        trace.omitted.update(omitted)
        trace.end_reason = data.get("end_reason")
        trace.vault_session_id = session_id
        return StoredSession(trace, data["state"], data["saved_at"], session_id)
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        raise VaultError("vault_invalid") from exc
    except VaultError:
        raise
    except Exception as exc:
        # Invalid archived contents cannot escape through UI/Diagnostics.
        raise VaultError("vault_invalid") from exc


@dataclass(repr=False)
class StoredSession:
    trace: PrivateHttpTrace
    state: str
    saved_at: str
    session_id: str

    @property
    def recovery_status(self) -> str:
        """Status after opening from disk; never claim a live recorder.

        Legacy 'recording' snapshots mean the app never wrote a final
        completion marker. The ciphertext is preserved as-is for forensics.
        """
        return "partial" if self.state == "recording" else self.state

    def private_summary(self) -> dict[str, object]:
        """Visible *only after explicit user unlock*; never for Diagnostics.

        saved_at is the timestamp of the LAST durable snapshot, not the
        beginning of the browsing session.
        """
        return {
            "id": self.session_id,
            "saved_at": self.saved_at,
            "status": self.recovery_status,
            "origin": self.trace.origins[0] if self.trace.origins else "",
            "exchanges": len(self.trace.exchanges),
            "omissions": dict(self.trace.omitted),
        }

    def __repr__(self):
        return (f"StoredSession(state={self.recovery_status!r}, "
                f"exchanges={len(self.trace.exchanges)})")


class PrivateSessionVault:
    """Directory trusted by Core, but its final component never follows links."""

    def __init__(self, directory: Path):
        self.path = Path(directory)

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

    @staticmethod
    def _entries(fd):
        total = 0
        count = 0
        try:
            for filename in os.listdir(fd):
                if not filename.endswith(".cbrv") or not _ID.fullmatch(
                    filename[:-5]
                ):
                    continue
                info = os.stat(filename, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise VaultError("vault_unsafe_file")
                count += 1
                total += info.st_size
            return count, total
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
            return tuple(sorted(ids))
        finally:
            os.close(fd)

    def unlocked_index(self, passphrase: str) -> tuple[tuple[dict, ...], int]:
        """Read an opt-in private session index, never persist an index file.

        No filename or ordinary list view exposes an origin, book, date or
        status. Each summary comes from a fully AEAD-authenticated session,
        after verifying the supplied passphrase; locked/tampered sessions do
        not block other, independently decryptable sessions. Call this API
        in a worker, as scrypt may take seconds across many sessions.
        """
        if not isinstance(passphrase, str) or not 12 <= len(passphrase) <= 1024:
            raise VaultError("vault_passphrase_required")
        ids = self.list_ids()
        if len(ids) > MAX_SESSIONS:
            raise VaultError("vault_limit")
        summaries = []
        unavailable = 0
        for identity in ids:
            try:
                item = self.open(identity, passphrase)
            except VaultError:
                unavailable += 1
                continue
            try:
                summaries.append(item.private_summary())
            finally:
                item.trace.destroy()
        summaries.sort(key=lambda entry: (
            entry["saved_at"], entry["id"]
        ), reverse=True)
        return tuple(summaries), unavailable

    def begin(self, passphrase: str):
        """Explicit action *before* capture; fail closed before Chromium runs."""
        salt = secrets.token_bytes(16)
        key = _key(passphrase, salt)
        _aead()
        fd = self._open_dir(create=True)
        try:
            count, total = self._entries(fd)
            if count >= MAX_SESSIONS or total >= MAX_VAULT_BYTES:
                raise VaultError("vault_limit")
            disk = os.fstatvfs(fd)
            if disk.f_bavail * disk.f_frsize < 8 * 1024 * 1024:
                raise VaultError("vault_limit")
            return SessionWriter(self, secrets.token_hex(16), salt, key)
        finally:
            os.close(fd)

    def open(self, session_id: str, passphrase: str) -> StoredSession:
        if type(session_id) is not str or not _ID.fullmatch(session_id):
            raise VaultError("vault_invalid")
        fd = self._open_dir(create=False)
        try:
            name = session_id + ".cbrv"
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(name, flags, dir_fd=fd)
            try:
                info = os.fstat(file_fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or not 64 <= info.st_size <= MAX_FILE):
                    raise VaultError("vault_unsafe_file")
                with os.fdopen(file_fd, "rb", closefd=False) as stream:
                    data = stream.read(MAX_FILE + 1)
            finally:
                os.close(file_fd)
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc
        finally:
            os.close(fd)
        if not data.startswith(MAGIC) or len(data) > MAX_FILE:
            raise VaultError("vault_invalid")
        salt = data[len(MAGIC):len(MAGIC) + 16]
        nonce = data[len(MAGIC) + 16:len(MAGIC) + 28]
        ciphertext = data[len(MAGIC) + 28:]
        key = _key(passphrase, salt)
        try:
            raw = _aead()(key).decrypt(
                nonce, ciphertext, MAGIC + salt + nonce + bytes.fromhex(session_id),
            )
        except Exception as exc:
            raise VaultError("vault_unlock_failed") from exc
        return _unrecord(raw, session_id)

    def delete(self, session_id: str, passphrase: str, *, confirmed=False):
        if confirmed is not True:
            raise VaultError("vault_confirmation_required")
        session = self.open(session_id, passphrase)
        session.trace.destroy()
        fd = self._open_dir(create=False)
        try:
            os.unlink(session_id + ".cbrv", dir_fd=fd)
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

    def save(self, trace: PrivateHttpTrace, state="recording", *, force=False):
        if self._sealed:
            raise VaultError("vault_sealed")
        if state not in _STATES:
            raise VaultError("vault_invalid")
        if (not force and state == "recording"
                and monotonic() - self._last < 1.0):
            return
        plaintext = _record(trace, self.session_id, state)
        nonce = secrets.token_bytes(12)
        ciphertext = _aead()(self._key).encrypt(
            nonce, plaintext, MAGIC + self._salt + nonce
            + bytes.fromhex(self.session_id),
        )
        data = MAGIC + self._salt + nonce + ciphertext
        if len(data) > MAX_FILE:
            raise VaultError("vault_limit")
        fd = self.vault._open_dir(create=False)
        tmp = "." + self.session_id + "-" + secrets.token_hex(8) + ".part"
        dest = self.session_id + ".cbrv"
        try:
            count, total = self.vault._entries(fd)
            if total + len(data) > MAX_VAULT_BYTES or (
                count >= MAX_SESSIONS and not self.session_id in
                [name[:-5] for name in os.listdir(fd) if name.endswith(".cbrv")]
            ):
                raise VaultError("vault_limit")
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
                trace.vault_session_id = self.session_id
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_write_failed") from exc
        finally:
            os.close(fd)
