"""#3882: offline CLI audit transport, never opens or migrates mutable Core."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import RLock
from types import SimpleNamespace
from typing import Any

from .acquisition_audit import audit_account
from .sync_cli_contract import require_unique_sync_account, SyncSelectionError
from ..paths import database_file, config_file


class ReadOnlyStorage:
    """SQLite mode=ro prevents schema migration, writes and implicit creation."""
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()

    def _connect(self):
        if not self.path.is_file() or self.path.is_symlink():
            raise OSError("audit_database_unavailable")
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)


def _library_root() -> Path:
    """Read the configuration bytes without constructing Config (chmod/migration)."""
    configured = "~/EBooks"
    path = config_file(create=False)
    if path.exists() and not path.is_symlink():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            node = value.get("library") if type(value) is dict else None
            requested = node.get("directory") if type(node) is dict else None
            if type(requested) is str and requested.strip():
                configured = requested
        except (OSError, ValueError, UnicodeError):
            raise ValueError("audit_configuration_unavailable") from None
    return Path(os.path.expandvars(os.path.expanduser(configured))).absolute()


def _resolve_source(storage, *, provider: str, identity: str,
                    credential: str, account_id: str | None) -> Any:
    if not identity or not provider:
        raise SyncSelectionError("source_scope_required")
    with storage._lock, storage._connect() as db:
        rows = db.execute(
            "SELECT a.identity_id,a.provider_id,a.credential_id,a.account_id "
            "FROM provider_accounts a JOIN provider_connections c "
            "ON c.connection_id=a.connection_id AND c.identity_id=a.identity_id "
            "AND c.provider_id=a.provider_id AND c.credential_id=a.credential_id "
            "WHERE c.enabled=1 AND a.identity_id=? AND a.provider_id=?",
            (identity, provider),
        ).fetchall()
    accounts = [SimpleNamespace(identity_id=x[0], provider_id=x[1],
                                credential_id=x[2], unique_id=x[3]) for x in rows]
    return require_unique_sync_account(
        accounts, provider=provider, identity=identity,
        credential=credential, account_id=account_id,
    )


def private_json_output(path: Path, payload: dict[str, Any]) -> None:
    """Atomic 0600 JSON, no symlink following or overwrite."""
    if path.suffix.lower() != ".json":
        raise ValueError("audit_output_suffix_required")
    if os.path.lexists(path):
        raise FileExistsError("audit_output_exists")
    if not path.parent.is_dir():
        raise ValueError("audit_output_parent_missing")
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("audit_output_limit")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(
            dir=path.parent, prefix=".cifereca-audit-", suffix=".part",
        )
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def offline_acquisition_audit(
    *, provider: str, identity: str, credential: str = "",
    account_id: str | None = None, verify_files: bool = False,
    output: Path | None = None,
) -> dict[str, Any]:
    """Return one safe report; select before any filesystem hash or HTTP."""
    if output is not None and os.path.lexists(output):
        raise FileExistsError("audit_output_exists")
    storage = ReadOnlyStorage(database_file(create=False))
    resolved = _resolve_source(
        storage, provider=provider, identity=identity,
        credential=credential, account_id=account_id,
    )
    audit = audit_account(
        storage, identity_id=resolved.identity_id,
        provider_id=resolved.provider_id,
        credential_id=resolved.credential_id,
        account_id=resolved.unique_id,
        library_root=_library_root(),
        staging_roots=(storage.path.parent / "acquisition-work",),
        verify_files=verify_files,
    )
    if output is not None:
        private_json_output(output, audit)
    return audit
