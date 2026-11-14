"""P0 #5975: supplier-neutral manual source confirmation is capability driven.

Synthetic headless sources never GET, mutate SQLite or grant acquisition.
The historical adapter-only owner alias is opt-in, not a Core brand branch.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import source_materialization as materialize
from cifereca.models import Account


def _harness(tmp_path, *, provider="future-books", enabled=True, backup=True,
             session_owner="alice", alias=None, is_primary=True):
    account = Account(
        provider_id=provider, credential_id="future-card", identity_id="alice",
        unique_id="account-A", is_primary=is_primary,
    )
    class SyntheticSession:
        legacy_account_identity_alias_v1 = alias

        def __init__(self):
            self.provider_id = provider
            self.credential_id = "future-card"
            self.connected = True
            self.capabilities = NS(local_materialization=enabled, backup=backup)
            self.accounts = [Account(
                provider_id=provider, credential_id="future-card",
                identity_id=session_owner, unique_id="account-A",
                is_primary=is_primary,
            )]

        def backup_manifest(self):
            pytest.fail("Never call legacy manifest for manual confirmation")

    session = SyntheticSession()
    root = tmp_path / "library"
    owner = NS(identity_id="alice", directory_name="Alice")
    storage = NS(
        identity_for_connection=lambda pid, cid: (
            "alice" if (pid, cid) == (provider, "future-card") else None
        ),
        metadata_get=lambda _key: None,
    )
    service = NS(
        accounts=[account],
        _sessions={f"{provider}:future-card": session},
        _session_key=lambda pid, cid: f"{pid}:{cid}",
        storage=storage, config=NS(get=lambda key, default=None: (
            str(root) if key == "library.directory" else default
        )),
        identities=[owner],
        identity_by_id=lambda name: owner if name == "alice" else None,
        library_dir=lambda: root,
    )
    broker = NS(service=service, _source_id=lambda _account: "scope-A",
                _enabled=lambda _source: True)
    return broker, account, session, root


def test_future_provider_with_declared_capability_admitted_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(materialize, "acquisition_lock_file", lambda: tmp_path / "lock")
    broker, account, session, library = _harness(tmp_path)
    result = materialize._source(broker, "scope-A", allow_canonical=True)
    assert result[0] is account and result[1] is session
    assert result[2] == ("scope-A", "alice", "future-books", "future-card", "account-A")
    assert type(result[3]) is str and len(result[3]) == 64
    assert not library.exists()
    # A partial/legacy delivery ABI cannot produce an acquisition token.
    assert not materialize.prepare(broker, "scope-A").ok
    assert materialize.prepare(broker, "scope-A").facts["status"] == "canonical_delivery_unavailable"


@pytest.mark.parametrize("enabled,backup", [(False, True), (False, False)])
def test_backup_only_and_undeclared_sources_refuse(tmp_path, enabled, backup):
    broker, _account, _session, library = _harness(
        tmp_path, enabled=enabled, backup=backup,
    )
    with pytest.raises(materialize._Rejected, match="not_supported"):
        materialize._source(broker, "scope-A", allow_canonical=True)
    assert not library.exists()


def test_canonical_capability_does_not_require_legacy_backup_flag(tmp_path):
    broker, account, _session, root = _harness(
        tmp_path, enabled=True, backup=False,
    )
    selected = materialize._source(broker, "scope-A", allow_canonical=True)
    assert selected[0] is account
    assert selected[2][2] == "future-books"
    assert not root.exists()


@pytest.mark.parametrize("alias,expected", [
    (None, "ambiguous_account"),
    ("different", "ambiguous_account"),
    ("default", None),
])
def test_legacy_default_alias_requires_explicit_supplier_declaration(
    tmp_path, alias, expected,
):
    broker, account, session, _root = _harness(
        tmp_path, session_owner="default", alias=alias,
    )
    if expected:
        with pytest.raises(materialize._Rejected, match=expected):
            materialize._source(broker, "scope-A", allow_canonical=True)
    else:
        assert materialize._source(broker, "scope-A", allow_canonical=True)[0] is account
    assert session.accounts[0].identity_id == "default"


def test_nonprimary_default_alias_never_attests_owner(tmp_path):
    broker, _account, _session, _root = _harness(
        tmp_path, session_owner="default", alias="default", is_primary=False,
    )
    with pytest.raises(materialize._Rejected, match="ambiguous_account"):
        materialize._source(broker, "scope-A", allow_canonical=True)


def test_unrelated_legacy_backup_settings_do_not_select_canonical_roots(tmp_path):
    broker, account, _session, root = _harness(tmp_path)
    original = materialize._source(broker, "scope-A", allow_canonical=True)
    broker.service.config.get = lambda key, default=None: (
        str(root) if key == "library.directory" else "foreign-legacy-backup"
    )
    changed = materialize._source(broker, "scope-A", allow_canonical=True)
    assert original[3] == changed[3]
    assert original[0] is account
    assert not root.exists()


def test_canonical_confirmation_core_has_no_supplier_dispatch_or_backup_path_import():
    source = inspect.getsource(materialize._source)
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Constant) and node.value in {"kobo", "numilog", "paris"}
        for node in ast.walk(tree)
    )
    assert "local_materialization" in source
    module = ast.parse(Path(materialize.__file__).read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "profile_source_paths"
        for node in ast.walk(module)
    )
