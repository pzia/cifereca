"""Permanent Core capture service and exact, producer-bound broker ports.

Uses the single extracted storage engine. Consumers own their observation codec
and consent UI; the service owns keys, filesystem, producer binding and scope.
No provider execution, network access, export or capture activation occurs here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock

from .capture_key import CaptureKey
from .capture_vault import EncryptedCaptureVault, VaultError, MAX_FILE
from ..plugin_api.capture_vault import CaptureScope, CaptureSummary, CapturedSession

_ALLOWED = frozenset(("ui.page.browser-recorder", "ui.page.diagnostics"))
_STATES = frozenset(("recording", "completed", "interrupted"))
_SCHEMA = "cifereca.capture-vault/1"
_HEADER_LIMIT = 8192
MAX_PAYLOAD = MAX_FILE - _HEADER_LIMIT - 128


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
    @staticmethod
    def _encode(capture, session_id, state):
        if (type(capture) is not _Capture or type(capture.payload) is not bytes
                or len(capture.payload) > MAX_PAYLOAD or state not in _STATES):
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
                    or len(payload) > MAX_PAYLOAD
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
    def __init__(self, directory: Path, key_directory: Path, *, authorize_scope):
        self._storage = _Storage(directory)
        self._key_source = CaptureKey(key_directory)
        self._authorize_scope = authorize_scope
        self._lock = RLock()

    def for_plugin(self, plugin_id: str, *, trusted: bool = False):
        if plugin_id not in _ALLOWED:
            return None
        return _Port(self, plugin_id)

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
    def __init__(self, service, producer):
        self._service = service
        self._producer = producer

    def begin(self, scope, source_session_ref, *, consented=False):
        if consented is not True:
            raise VaultError("vault_confirmation_required")
        self._service._check(self._producer, scope)
        if not _identifier(source_session_ref):
            raise VaultError("vault_scope_invalid")
        with self._service._lock:
            key = self._service._key(create=True)
            writer = self._service._storage.begin(key)
            return _Writer(self, scope, source_session_ref, writer, key)

    def _open(self, scope, capture_id, key):
        stored = self._service._storage.open(capture_id, key)
        if stored.producer != self._producer or stored.summary.scope != scope:
            self._service._storage._release(stored)
            raise VaultError("vault_scope_denied")
        return stored

    def sessions(self, scope):
        self._service._check(self._producer, scope)
        with self._service._lock:
            ids = self._service._storage.list_ids()
            if not ids:
                return ()
            key = self._service._key()
            rows = []
            for sid in ids:
                stored = self._service._storage.open(sid, key)
                try:
                    if stored.producer == self._producer and stored.summary.scope == scope:
                        rows.append(stored.summary)
                finally:
                    self._service._storage._release(stored)
            return tuple(sorted(rows, key=lambda row: (row.saved_at, row.capture_id), reverse=True))

    def read(self, scope, capture_id):
        self._service._check(self._producer, scope)
        with self._service._lock:
            stored = self._open(scope, capture_id, self._service._key())
            try:
                return CapturedSession(stored.summary, stored.payload)
            finally:
                self._service._storage._release(stored)

    def delete(self, scope, capture_id, *, confirmed=False):
        if confirmed is not True:
            raise VaultError("vault_confirmation_required")
        self._service._check(self._producer, scope)
        with self._service._lock:
            key = self._service._key()
            stored = self._open(scope, capture_id, key)
            self._service._storage._release(stored)
            self._service._storage.delete(capture_id, key, confirmed=True)


class _Writer:
    def __init__(self, port, scope, source_session_ref, writer, key):
        self._port, self._scope, self._source_session_ref = port, scope, source_session_ref
        self._writer, self._key = writer, key

    @property
    def capture_id(self):
        return self._writer.session_id

    def save(self, payload, state="recording", *, force=False):
        service = self._port._service
        service._check(self._port._producer, self._scope)
        with service._lock:
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
