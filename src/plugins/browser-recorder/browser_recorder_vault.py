"""Opt-in, authenticated local vault for *observed* private HTTP traces.

Not a HAR, ZIP export, browser profile, or complete wire capture. Private URLs,
queries, redirects and captured text are encrypted before the first file write.
No operations occur at import time or while simply opening the plugin page.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from hashlib import sha256
from datetime import datetime, timezone, timedelta
import fcntl
import unicodedata
import json
import os
from pathlib import Path
import re
import secrets
import stat
from time import monotonic, time

from browser_recorder_http import (
    MAX_BODY_BYTES, MAX_EXCHANGES, MAX_TOTAL_BODY_BYTES,
    PrivateExchange, PrivateHttpTrace, _BODY_REASONS,
)

SCHEMA = "cifereca.browser-recorder.private-session/1"
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
        "content_negotiation": trace.content_negotiation,
        "compressed_responses_accepted": trace.allow_compressed_responses,
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
        strategy = data.get("content_negotiation", "browser_default")
        if type(strategy) is not str or strategy not in {"browser_default", "identity_requested"}:
            raise ValueError("invalid capture strategy")
        trace.content_negotiation = strategy
        accepted = data.get("compressed_responses_accepted", False)
        if type(accepted) is not bool:
            raise ValueError("invalid capture strategy")
        trace.allow_compressed_responses = accepted
        if type(rows) is not list or len(rows) > MAX_EXCHANGES:
            raise ValueError("invalid entries")
        for index, row in enumerate(rows):
            # Old CFRVLT01 sessions predate per-direction reason codes.
            # Accept only those exact legacy fields, never arbitrary data.
            legacy_fields = _FIELD_NAMES - {
                "request_body_reason", "response_body_reason",
            }
            if type(row) is not dict or set(row) not in (
                _FIELD_NAMES, legacy_fields,
            ):
                raise ValueError("invalid entry")
            if any(
                type(row[key]) is not str or row[key] not in _BODY_REASONS
                for key in ("request_body_reason", "response_body_reason")
                if key in row
            ):
                raise ValueError("invalid body reason")
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



@dataclass(frozen=True, repr=False)
class RetentionPlan:
    """Ephemeral authenticated deletion preview; never persisted to disk.

    Fingerprints bind user's consent to *exact* encrypted source and label
    snapshots. Do not include HTTP contents, origins, or names in this object.
    """
    older_than_days: int
    cutoff_utc: str
    fingerprints: tuple[tuple[str, str, str | None], ...]
    total_bytes: int

    @property
    def count(self) -> int:
        return len(self.fingerprints)


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
    def _staging(fd):
        """Recognize only our opaque write staging files, without following links."""
        found = []
        for name in os.listdir(fd):
            if _STAGE.fullmatch(name):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or not 0 <= info.st_size <= MAX_FILE):
                    raise VaultError("vault_unsafe_file")
                found.append((name, info))
        return tuple(found)

    @staticmethod
    def _entries(fd):
        """Count encrypted archives, labels AND orphan writes toward the quota."""
        total = count = 0
        try:
            staging = dict(PrivateSessionVault._staging(fd))
            for filename in os.listdir(fd):
                if filename in staging:
                    info = staging[filename]
                elif (filename.endswith(".cbrv") or filename.endswith(".cbrn")):
                    if not _ID.fullmatch(filename[:-5]):
                        continue
                    info = os.stat(filename, dir_fd=fd, follow_symlinks=False)
                    max_size = MAX_FILE if filename.endswith(".cbrv") else MAX_LABEL_FILE
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

    def staging_summary(self) -> tuple[int, int, int]:
        """Opaque (count, bytes, age-eligible count); never write or decrypt."""
        if not self.path.exists() and not self.path.is_symlink():
            return (0, 0, 0)
        fd = self._open_dir(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            rows = self._staging(fd)
            now = time()
            return (len(rows), sum(info.st_size for _, info in rows),
                    sum(now - info.st_mtime >= MIN_STAGING_AGE_SECONDS
                        for _, info in rows))
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc
        finally:
            os.close(fd)

    def prune_staging(self, *, confirmed: bool = False) -> tuple[int, int]:
        """Only manually delete stale encrypted .part/.labelpart, never sessions.

        Minimum age 24 h; directory-wide lock coordinates with writers and
        deletion. Any unsafe file aborts before the first mutation.
        """
        if confirmed is not True:
            raise VaultError("vault_confirmation_required")
        fd = self._open_dir(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            rows = self._staging(fd)
            now = time()
            eligible = [(name, info) for name, info in rows
                        if now - info.st_mtime >= MIN_STAGING_AGE_SECONDS]
            for name, _ in eligible:
                os.unlink(name, dir_fd=fd)
            if eligible:
                os.fsync(fd)
            return len(eligible), sum(info.st_size for _, info in eligible)
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc
        finally:
            os.close(fd)

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

    def get_label(self, session_id: str, passphrase: str) -> str:
        """Read AEAD sidecar after authenticating the immutable capture."""
        session = self.open(session_id, passphrase)
        session.trace.destroy()
        fd = self._open_dir(create=False)
        try:
            try:
                handle = os.open(
                    session_id + ".cbrn",
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd,
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
            plaintext = _aead()(_key(passphrase, salt)).decrypt(
                nonce, data[36:], LABEL_MAGIC + salt + nonce
                + bytes.fromhex(session_id),
            )
            return self._label_value(plaintext.decode("utf-8"))
        except Exception as exc:
            raise VaultError("vault_label_invalid") from exc

    def set_label(self, session_id: str, passphrase: str, label: str) -> None:
        """Atomic, separately encrypted metadata; never change .cbrv."""
        label = self._label_value(label)
        # Authentication must occur *under* the same directory lock as
        # replacing the sidecar. Otherwise a concurrent delete/retention
        # could remove the archive after the check and this method would
        # resurrect an orphan encrypted name for a deleted session.
        salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
        encrypted = _aead()(_key(passphrase, salt)).encrypt(
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
            self.get_label(session_id, passphrase)
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
            if total - previous_size + len(data) > MAX_VAULT_BYTES:
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
                summary = item.private_summary()
                # A forged/invalid sidecar does not poison other sessions.
                try:
                    summary["label"] = self.get_label(identity, passphrase)
                except VaultError:
                    summary["label"] = ""
                    summary["label_unavailable"] = True
                summaries.append(summary)
            finally:
                item.trace.destroy()
        summaries.sort(key=lambda entry: (
            entry["saved_at"], entry["id"]
        ), reverse=True)
        return tuple(summaries), unavailable

    @staticmethod
    def _retention_days(days: int) -> int:
        # The UI supplies a fixed selection, never a user-controlled path or
        # an arbitrary date string. Partial/interrupted remain excluded.
        if type(days) is not int or days not in {30, 90, 180, 365}:
            raise VaultError("vault_retention_invalid")
        return days

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
                name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd,
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
        capture = self._retention_bytes(fd, sid + ".cbrv", MAX_FILE)
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

    def retention_preview(self, passphrase: str, older_than_days: int,
                          *, now: datetime | None = None) -> RetentionPlan:
        """Preview only *completed* authenticated older sessions; no writes.

        Wrong keys and partial/interrupted sessions are never candidates.
        Reject unsafe/corrupt files rather than guessing whether to delete.
        """
        days = self._retention_days(older_than_days)
        if type(passphrase) is not str or not 12 <= len(passphrase) <= 1024:
            raise VaultError("vault_passphrase_required")
        now = (now if now is not None else datetime.now(timezone.utc))
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise VaultError("vault_retention_invalid")
        cutoff = now.astimezone(timezone.utc) - timedelta(days=days)
        fd = self._open_dir(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            ids = self.list_ids()
            if len(ids) > MAX_SESSIONS:
                raise VaultError("vault_limit")
            rows, total = [], 0
            for sid in ids:
                try:
                    stored = self.open(sid, passphrase)
                except VaultError as exc:
                    if str(exc) == "vault_unlock_failed":
                        continue  # another passphrase is not authorization
                    raise
                try:
                    if (stored.recovery_status != "completed"
                            or self._retention_date(stored.saved_at) >= cutoff):
                        continue
                    pair, size = self._retention_fingerprint(fd, sid)
                    # A label is user-private AEAD metadata, not an arbitrary
                    # encrypted blob that the retention planner may discard.
                    # Even a size/mode-valid sidecar with a different key,
                    # corrupt tag or swapped session AAD must fail closed.
                    if pair[2] is not None:
                        self.get_label(sid, passphrase)
                    rows.append(pair)
                    total += size
                finally:
                    stored.trace.destroy()
            return RetentionPlan(days, cutoff.isoformat(),
                                 tuple(sorted(rows)), total)
        finally:
            os.close(fd)

    def retention_apply(self, plan: RetentionPlan, passphrase: str, *,
                        confirmed: bool = False) -> tuple[int, int]:
        """Apply ONLY the exact preview after explicit independent consent.

        Validate every entry under the writer lock before the first unlink.
        Any replaced snapshot/label, unsafe link, wrong key or expired
        preview aborts entirely. No background/automatic retention.
        """
        if confirmed is not True:
            raise VaultError("vault_confirmation_required")
        if type(plan) is not RetentionPlan:
            raise VaultError("vault_retention_invalid")
        days = self._retention_days(plan.older_than_days)
        cutoff = self._retention_date(plan.cutoff_utc)
        if (not 0 < len(plan.fingerprints) <= MAX_SESSIONS
                or type(plan.total_bytes) is not int
                or plan.total_bytes <= 0):
            raise VaultError("vault_retention_invalid")
        utc_now = datetime.now(timezone.utc)
        # Plans are only valid for 10 minutes after the requested cutoff,
        # never for a past or future date. No silently evolving candidates.
        proposed_at = cutoff + timedelta(days=days)
        if not timedelta(0) <= utc_now - proposed_at <= timedelta(minutes=10):
            raise VaultError("vault_retention_expired")
        fd = self._open_dir(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            seen, total = set(), 0
            for sid, digest, label_digest in plan.fingerprints:
                if (sid in seen or type(digest) is not str
                        or len(digest) != 64):
                    raise VaultError("vault_retention_invalid")
                seen.add(sid)
                actual, size = self._retention_fingerprint(fd, sid)
                if actual != (sid, digest, label_digest):
                    raise VaultError("vault_retention_changed")
                if label_digest is not None:
                    self.get_label(sid, passphrase)
                session = self.open(sid, passphrase)
                try:
                    if (session.recovery_status != "completed"
                            or self._retention_date(session.saved_at) >= cutoff):
                        raise VaultError("vault_retention_changed")
                finally:
                    session.trace.destroy()
                total += size
            if total != plan.total_bytes:
                raise VaultError("vault_retention_changed")
            # Full preflight passed. No page can silently trigger this call.
            for sid, _digest, label_digest in plan.fingerprints:
                if label_digest is not None:
                    os.unlink(sid + ".cbrn", dir_fd=fd)
                os.unlink(sid + ".cbrv", dir_fd=fd)
            os.fsync(fd)
            return len(seen), total
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_unsafe_file") from exc
        finally:
            os.close(fd)

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
        fd = self._open_dir(create=False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            session = self.open(session_id, passphrase)
            session.trace.destroy()
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
            if sidecar is not None:
                os.unlink(session_id + ".cbrn", dir_fd=fd)
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
            previous_size = old.st_size if old is not None else 0
            if (total - previous_size + len(data) > MAX_VAULT_BYTES
                    or (count >= MAX_SESSIONS and old is None)):
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
                trace.vault_session_id = self.session_id
        except VaultError:
            raise
        except OSError as exc:
            raise VaultError("vault_write_failed") from exc
        finally:
            os.close(fd)
