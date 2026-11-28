"""Permanent Core capture service and exact, producer-bound broker ports.

Uses the single extracted storage engine. Consumers own their observation codec
and consent UI; the service owns keys, filesystem, producer binding and scope.
No provider execution, network access, export or capture activation occurs here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Event, RLock, Thread

from .capture_key import CaptureKey
from .capture_vault import EncryptedCaptureVault, VaultError, MAX_FILE, MAX_SESSIONS
from ..plugin_api.capture_vault import CaptureScope, CaptureSummary, CapturedSession

_ALLOWED = frozenset(("ui.page.browser-recorder", "ui.page.diagnostics"))
_STATES = frozenset(("recording", "completed", "interrupted"))
_SCHEMA = "cifereca.capture-vault/1"
_HEADER_LIMIT = 8192
MAX_PAYLOAD = MAX_FILE - _HEADER_LIMIT - 128
# HTTP Trace has a 32 MiB session budget; the bounded ZIP adds index overhead.
MAX_HTTP_FILE = 64 * 1024 * 1024
MAX_HTTP_PAYLOAD = MAX_HTTP_FILE - _HEADER_LIMIT - 128


def _payload_limit(producer):
    return MAX_HTTP_PAYLOAD if producer == "ui.page.diagnostics" else MAX_PAYLOAD
DEFAULT_RETENTION_DAYS = 90
DEFAULT_VOLUME_BYTES = 128 * 1024 * 1024
MIN_VOLUME_BYTES = 8 * 1024 * 1024
MAX_PIN_DAYS = 30
MAX_PINS = 16


def validate_retention_policy(days, volume):
    if (type(days) is not int or not 1 <= days <= 365
            or type(volume) is not int or not MIN_VOLUME_BYTES <= volume <= DEFAULT_VOLUME_BYTES):
        raise VaultError("vault_retention_invalid")
    return days, volume


def _metadata(value):
    if not value:
        return "", ""
    try:
        label, until = json.loads(value)
        if (type(label) is not str or len(label) > 80 or not all(c.isprintable() for c in label)
                or type(until) is not str):
            raise ValueError
        if until:
            parsed = datetime.fromisoformat(until)
            if parsed.tzinfo is None:
                raise ValueError
        return label, until
    except (ValueError, TypeError, OverflowError) as exc:
        raise VaultError("vault_label_invalid") from exc


def _pinned(value, now):
    _, until = _metadata(value)
    return bool(until and datetime.fromisoformat(until) > now)


def _identifier(value: object, *, optional=False) -> bool:
    return (type(value) is str and len(value) <= 256
            and (optional or bool(value)) and value == value.strip()
            and all(char.isprintable() for char in value))


def _validate_scope(scope: CaptureScope) -> None:
    if type(scope) is not CaptureScope:
        raise VaultError("vault_scope_invalid")
    values = (scope.provider_id, scope.instance_id, scope.account_id, scope.identity_id)
    if not all(_identifier(value, optional=True) for value in values):
        raise VaultError("vault_scope_invalid")
    # Manual/unbound captures and exact connected accounts are separate.
    if any(values) and not all(values):
        raise VaultError("vault_scope_invalid")


@dataclass(repr=False)
class _Capture:
    producer: str
    scope: CaptureScope
    source_session_ref: str
    payload: bytes


@dataclass(repr=False)
class _Stored:
    producer: str
    summary: CaptureSummary
    payload: bytes

    @property
    def saved_at(self):
        return self.summary.saved_at

    @property
    def recovery_status(self):
        return "partial" if self.summary.state == "recording" else self.summary.state

    def private_summary(self):
        return {"id": self.summary.capture_id, "saved_at": self.saved_at}


class _Storage(EncryptedCaptureVault):
    def __init__(self, path, policy):
        super().__init__(path)
        self._policy = policy

    @property
    def max_file(self):
        return MAX_HTTP_FILE

    @property
    def max_vault_bytes(self):
        return self._policy()[1]

    @staticmethod
    def _label_value(value):
        # The existing authenticated label sidecar stores both the label and
        # the expiring pin. No separate persistent index or pin store.
        if type(value) is not str or len(value.encode("utf-8")) > 800:
            raise VaultError("vault_label_invalid")
        _metadata(value)
        return value

    @staticmethod
    def _encode(capture, session_id, state):
        if (type(capture) is not _Capture or type(capture.payload) is not bytes
                or len(capture.payload) > _payload_limit(capture.producer) or state not in _STATES):
            raise VaultError("vault_limit")
        _validate_scope(capture.scope)
        if capture.producer not in _ALLOWED or not _identifier(capture.source_session_ref):
            raise VaultError("vault_scope_invalid")
        header = json.dumps({
            "schema": _SCHEMA, "id": session_id, "producer": capture.producer,
            "scope": asdict(capture.scope), "source_session_ref": capture.source_session_ref,
            "state": state, "saved_at": datetime.now(timezone.utc).isoformat(),
            "bytes": len(capture.payload), "sha256": sha256(capture.payload).hexdigest(),
        }, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        if len(header) > _HEADER_LIMIT:
            raise VaultError("vault_limit")
        return len(header).to_bytes(4, "big") + header + capture.payload

    @staticmethod
    def _decode(data, session_id):
        try:
            size = int.from_bytes(data[:4], "big")
            if not 0 < size <= _HEADER_LIMIT or size + 4 > len(data):
                raise ValueError
            row = json.loads(data[4:4 + size])
            payload = data[4 + size:]
            if (row["schema"] != _SCHEMA or row["id"] != session_id
                    or row["producer"] not in _ALLOWED or row["state"] not in _STATES
                    or not _identifier(row["source_session_ref"])
                    or type(row["bytes"]) is not int or row["bytes"] != len(payload)
                    or len(payload) > _payload_limit(row["producer"])
                    or row["sha256"] != sha256(payload).hexdigest()):
                raise ValueError
            scope = CaptureScope(**row["scope"])
            _validate_scope(scope)
            timestamp = datetime.fromisoformat(row["saved_at"])
            if timestamp.tzinfo is None:
                raise ValueError
            summary = CaptureSummary(session_id, scope, row["source_session_ref"],
                                     row["state"], row["saved_at"], len(payload))
            return _Stored(row["producer"], summary, payload)
        except (ValueError, KeyError, TypeError, UnicodeError, OverflowError) as exc:
            raise VaultError("vault_invalid") from exc

    @staticmethod
    def _release(stored):
        stored.payload = b""

    @staticmethod
    def _mark_sealed(capture, session_id):
        pass


class CoreCaptureVault:
    """Composition-owned singleton; construction performs no capture I/O."""
    def __init__(self, directory: Path, key_directory: Path, *, authorize_scope,
                 policy=None, health=None):
        self._health = health
        self._policy_source = policy or (lambda: (DEFAULT_RETENTION_DAYS, DEFAULT_VOLUME_BYTES))
        self._storage = _Storage(directory, self._policy)
        self._key_source = CaptureKey(key_directory)
        self._authorize_scope = authorize_scope
        self._lock = RLock()
        self._stop = Event()
        self._maintenance = None
        # Core-owned, bounded quarantine of failed writes in this process.
        # Only the exact authenticated writer binding can be retried.
        self._failed_archives = {}

    def _discard_failed_archive(self, capture_id, binding):
        producer, scope, source_ref = binding
        if capture_id not in self._storage.list_ids():
            return
        key = self._key()
        stored = self._storage.open(capture_id, key)
        try:
            if (stored.producer != producer or stored.summary.scope != scope
                    or stored.summary.source_session_ref != source_ref
                    or stored.summary.state != "completed"):
                raise VaultError("vault_scope_denied")
        finally:
            self._storage._release(stored)
        self._storage.delete(capture_id, key, confirmed=True)

    def _retry_failed_archives(self):
        for capture_id, binding in tuple(self._failed_archives.items()):
            try:
                self._discard_failed_archive(capture_id, binding)
            except VaultError:
                # A physically unsafe/unreadable file must not be bypassed.
                continue
            self._failed_archives.pop(capture_id, None)

    def _policy(self):
        return validate_retention_policy(*self._policy_source())

    def maintain(self, *, reserve=False, reserve_bytes=None, now=None):
        with self._lock:
            self._retry_failed_archives()
            if not self._storage.path.exists() and not self._storage.path.is_symlink():
                return (0, 0)
            key = self._key()
            days, _ = self._policy()
            return self._storage.auto_prune(
                key, older_than_days=days, pinned=_pinned,
                reserve_bytes=(MAX_FILE if reserve else 0) if reserve_bytes is None else reserve_bytes,
                reserve_session=reserve, now=now,
            )

    def start_maintenance(self):
        # Do not create a directory or a key merely by opening Core.
        with self._lock:
            if (self._maintenance is not None or self._stop.is_set()
                    or not self._storage.path.exists()):
                return
            def work():
                while not self._stop.is_set():
                    try:
                        self.maintain()
                    except VaultError:
                        # Keep unreadable captures intact. Access/admission
                        # reports the concrete closed error to the consumer.
                        pass
                    if self._stop.wait(3600):
                        break
            self._maintenance = Thread(target=work, daemon=True,
                                       name="capture-retention")
            self._maintenance.start()

    def close(self):
        self._stop.set()
        if self._maintenance is not None:
            self._maintenance.join()

    def for_plugin(self, plugin_id: str, *, trusted: bool = False):
        if plugin_id not in _ALLOWED:
            return None
        cls = _DiagnosticsPort if plugin_id == "ui.page.diagnostics" else _Port
        return cls(self, plugin_id)

    def for_activation(self, plugin_id: str, *, current):
        if plugin_id not in _ALLOWED or current() is not True:
            return None
        cls = _DiagnosticsPort if plugin_id == "ui.page.diagnostics" else _Port
        return cls(self, plugin_id, current=current)

    def _key(self, *, create=False):
        # Never regenerate a lost key for an existing vault, including orphan
        # staging or labels. A trusted empty vault is the sole create case.
        if create:
            path = self._storage.path
            if path.exists() or path.is_symlink():
                fd = self._storage._open_dir(create=False)
                try:
                    create = not os.listdir(fd)
                finally:
                    os.close(fd)
        return self._key_source.load(create=create)

    def _check(self, producer, scope):
        _validate_scope(scope)
        if producer not in _ALLOWED or self._authorize_scope(producer, scope) is not True:
            raise VaultError("vault_scope_denied")


class _Port:
    def __init__(self, service, producer, *, current=lambda: True):
        self._service = service
        self._producer = producer
        self._current = current

    def _check(self, scope):
        if self._current() is not True or self._service._stop.is_set():
            raise VaultError("vault_activation_expired")
        self._service._check(self._producer, scope)

    def begin(self, scope, source_session_ref, *, consented=False):
        return self._begin(scope, source_session_ref, consented=consented, reserve_bytes=MAX_FILE)

    def _begin(self, scope, source_session_ref, *, consented=False, reserve_bytes):
        if consented is not True:
            raise VaultError("vault_confirmation_required")
        self._check(scope)
        if not _identifier(source_session_ref):
            raise VaultError("vault_scope_invalid")
        with self._service._lock:
            self._check(scope)
            key = self._service._key(create=True)
            if reserve_bytes > self._service._policy()[1]:
                raise VaultError("vault_limit")
            self._service.maintain(reserve=True, reserve_bytes=reserve_bytes)
            writer = self._service._storage.begin(key)
            self._service.start_maintenance()
            return _Writer(self, scope, source_session_ref, writer, key)

    def _open(self, scope, capture_id, key):
        if capture_id in self._service._failed_archives:
            raise VaultError("vault_scope_denied")
        stored = self._service._storage.open(capture_id, key)
        if stored.producer != self._producer or stored.summary.scope != scope:
            self._service._storage._release(stored)
            raise VaultError("vault_scope_denied")
        return stored

    def sessions(self, scope):
        self._check(scope)
        with self._service._lock:
            self._check(scope)
            ids = self._service._storage.list_ids()
            if not ids:
                return ()
            key = self._service._key()
            rows = []
            for sid in ids:
                if sid in self._service._failed_archives:
                    continue
                stored = self._service._storage.open(sid, key)
                try:
                    if stored.producer == self._producer and stored.summary.scope == scope:
                        label, until = _metadata(self._service._storage.get_label(sid, key))
                        rows.append(replace(stored.summary, label=label, pinned_until=until))
                finally:
                    self._service._storage._release(stored)
            return tuple(sorted(rows, key=lambda row: (row.saved_at, row.capture_id), reverse=True))

    def read(self, scope, capture_id):
        self._check(scope)
        with self._service._lock:
            self._check(scope)
            stored = self._open(scope, capture_id, self._service._key())
            try:
                key = self._service._key()
                label, until = _metadata(self._service._storage.get_label(capture_id, key))
                self._check(scope)
                return CapturedSession(replace(stored.summary, label=label, pinned_until=until),
                                       stored.payload)
            finally:
                self._service._storage._release(stored)

    def set_label(self, scope, capture_id, label):
        if (type(label) is not str or len(label) > 80 or not all(c.isprintable() for c in label)):
            raise VaultError("vault_label_invalid")
        self._update_metadata(scope, capture_id, label=label)

    def pin(self, scope, capture_id, *, pinned, confirmed=False):
        if confirmed is not True:
            raise VaultError("vault_confirmation_required")
        if type(pinned) is not bool:
            raise VaultError("vault_label_invalid")
        self._update_metadata(scope, capture_id, pinned=pinned)

    def _update_metadata(self, scope, capture_id, *, label=None, pinned=None):
        self._check(scope)
        with self._service._lock:
            self._check(scope)
            key = self._service._key()
            storage = self._service._storage
            stored = self._open(scope, capture_id, key)
            try:
                if pinned is True and stored.recovery_status == "partial":
                    raise VaultError("vault_pin_invalid")
            finally:
                storage._release(stored)
            now = datetime.now(timezone.utc)
            previous_label, previous_until = _metadata(storage.get_label(capture_id, key))
            until = previous_until
            if pinned is not None:
                until = ((now + timedelta(days=MAX_PIN_DAYS)).isoformat() if pinned else "")
            value = json.dumps([previous_label if label is None else label, until],
                               ensure_ascii=False, separators=(",", ":"))
            def guard(_fd):
                # Count pins under the existing filesystem writer lock.
                if pinned is True:
                    count = sum(
                        _pinned(storage.get_label(sid, key), now)
                        for sid in storage.list_ids() if sid != capture_id
                    )
                    if count >= MAX_PINS:
                        raise VaultError("vault_pin_limit")
            storage.set_label(capture_id, key, value, guard=guard)

    def delete(self, scope, capture_id, *, confirmed=False):
        if confirmed is not True:
            raise VaultError("vault_confirmation_required")
        self._check(scope)
        with self._service._lock:
            self._check(scope)
            key = self._service._key()
            stored = self._open(scope, capture_id, key)
            self._service._storage._release(stored)
            self._service._storage.delete(capture_id, key, confirmed=True)


class _DiagnosticsPort(_Port):
    """Only Diagnostics can archive the opt-in process-wide HTTP Trace."""

    def archive_http_trace(self, *, consented=False):
        if consented is not True:
            raise VaultError("vault_confirmation_required")
        from ..http_trace import HTTP_TRACE, build_http_trace_zip
        scope = CaptureScope()
        self._check(scope)
        if HTTP_TRACE.status()["active"] is not True:
            raise VaultError("vault_trace_inactive")
        snapshot = HTTP_TRACE.snapshot()
        source = snapshot["session_ref"]
        payload = build_http_trace_zip(snapshot)
        self._check(scope)
        if HTTP_TRACE.status()["active"] is not True:
            raise VaultError("vault_trace_inactive")
        writer = self._begin(scope, source, consented=True,
                             reserve_bytes=len(payload) + _HEADER_LIMIT + 128)
        writer.save(payload, "completed", force=True)
        return writer.capture_id

    def archive_search_trace(self, scope, source_ref, *, consented=False):
        if consented is not True:
            raise VaultError("vault_confirmation_required")
        from .provider_health_projection import Scope
        from ..http_trace import HTTP_TRACE, build_http_trace_zip
        self._check(scope)
        health = self._service._health
        exact = Scope(scope.provider_id, scope.instance_id, scope.account_id,
                      scope.identity_id, "search")
        if scope == CaptureScope() or health is None:
            raise VaultError("vault_scope_denied")
        with health.archive_guard(exact, source_ref) as lease:
            if lease is None:
                raise VaultError("vault_scope_denied")
            if HTTP_TRACE.status()["active"] is not True:
                raise VaultError("vault_trace_inactive")
            snapshot = HTTP_TRACE.snapshot()
            snapshot["exchanges"] = [entry for entry in snapshot["exchanges"]
                                     if entry.get("task_ref") == source_ref]
            if not snapshot["exchanges"] or any(
                    entry.get("outcome") == "in_progress" for entry in snapshot["exchanges"]):
                raise VaultError("vault_trace_incomplete")
            snapshot["session_ref"] = source_ref
            payload = build_http_trace_zip(snapshot)
            self._check(scope)
            if HTTP_TRACE.status()["active"] is not True:
                raise VaultError("vault_trace_inactive")
            # Keep the new object private to this operation until commit or
            # quarantine; neither readers nor maintenance can race rollback.
            service = self._service
            with service._lock:
                if len(service._failed_archives) >= MAX_SESSIONS:
                    raise VaultError("vault_archive_rollback_failed")
                writer = self._begin(scope, source_ref, consented=True,
                                     reserve_bytes=len(payload) + _HEADER_LIMIT + 128)
                try:
                    writer.save(payload, "completed", force=True)
                    self._check(scope)
                    if not health.link_capture(exact, source_ref, writer.capture_id,
                                               lease=lease, port=self, consented=True):
                        raise VaultError("vault_scope_denied")
                    self._check(scope)
                except Exception:
                    health.unlink_capture(exact, source_ref, writer.capture_id)
                    service._failed_archives[writer.capture_id] = (
                        self._producer, scope, source_ref)
                    try:
                        writer._rollback_completed()
                    except Exception as cleanup_error:
                        raise VaultError("vault_archive_rollback_failed") from cleanup_error
                    service._failed_archives.pop(writer.capture_id, None)
                    raise
                return writer.capture_id

    def export_http_archive(self, capture_id, destination, *, confirmed=False):
        if confirmed is not True:
            raise VaultError("vault_confirmation_required")
        from ..http_trace import HTTP_TRACE, project_archived_http_zip, HTTP_TRACE_ZIP_LIMIT
        from .capture_export import write_capture_zip
        scope = CaptureScope()
        row = self.read(scope, capture_id)
        try:
            payload = project_archived_http_zip(
                row.payload, include_binary=HTTP_TRACE.status()["include_binary"],
                source_session_ref=row.summary.source_session_ref,
                include_kit=True,
            )
        except Exception as exc:
            raise VaultError("vault_invalid") from exc
        self._check(scope)
        return write_capture_zip(Path(destination), payload, confirmed=True,
                                 max_bytes=HTTP_TRACE_ZIP_LIMIT)


class _Writer:
    def __init__(self, port, scope, source_session_ref, writer, key):
        self._port, self._scope, self._source_session_ref = port, scope, source_session_ref
        self._writer, self._key = writer, key

    @property
    def capture_id(self):
        return self._writer.session_id

    def _rollback_completed(self):
        service = self._port._service
        with service._lock:
            key = service._key()
            if key != self._key:
                raise VaultError("vault_key_unavailable")
            service._discard_failed_archive(
                self.capture_id, (self._port._producer, self._scope,
                                  self._source_session_ref))

    def save(self, payload, state="recording", *, force=False):
        service = self._port._service
        self._port._check(self._scope)
        with service._lock:
            self._port._check(self._scope)
            if service._key() != self._key:
                raise VaultError("vault_key_unavailable")
            self._writer.save(_Capture(self._port._producer, self._scope,
                                       self._source_session_ref, payload),
                              state, force=force)


def capture_scope_authorizer(library):
    def authorize(producer, scope):
        if scope == CaptureScope():
            return producer in _ALLOWED
        try:
            identity = library.identity_for_connection(scope.provider_id, scope.instance_id)
            if identity.identity_id != scope.identity_id:
                return False
            return any(
                account.provider_id == scope.provider_id
                and account.credential_id == scope.instance_id
                and account.unique_id == scope.account_id
                and account.identity_id == scope.identity_id
                for account in library.accounts_for_identity(scope.identity_id)
            )
        except Exception:
            return False
    return authorize
