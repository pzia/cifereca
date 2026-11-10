"""Retired Kobo W2 confirmation cannot grant acquisition authority (#4858).

Preserve exact-source preflight, token rejection and the absence of provider
I/O/SQLite mutation. Canonical A→B admission is covered by its own suites.
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import source_materialization as materialize
from cifereca.core import kobo_backup
from cifereca.identities import Identity
from cifereca.locking import InterProcessLock
from cifereca.models import Account
from tests.test_kobo_backup_fault_isolation import _service, _write_epub, _ready_adept_state, BackupSession


@pytest.fixture
def case(tmp_path, monkeypatch):
    manifest = [{"product_id": "PRIVATE-book-1", "revision_id": "r1", "title": "PRIVATE title", "format": "EPUB"},
                {"product_id": "PRIVATE-book-2", "revision_id": "r2", "title": "PRIVATE second", "format": "EPUB"}]
    service, session = _service(tmp_path, manifest)
    service.accounts = list(session.accounts)
    sessions = [session]
    # The selected source is the middle of three identities/accounts; current
    # identity stays default, so accidentally using it is observable.
    for index in (2, 3):
        identity = Identity(f"reader-{index}", f"Reader {index}", directory_name=f"reader-{index}")
        service.identities.append(identity)
        credential = f"vault-{index}"
        service.storage.provider_connection_put({"identity_id": identity.identity_id, "provider_id": "kobo",
                                                 "credential_id": credential, "label": "Private account"})
        other = BackupSession(descriptor=session.descriptor, credential_id=credential, label="Kobo",
                              credentials={}, options={}, manifest=manifest)
        other.connect()
        other.accounts[0].identity_id = identity.identity_id
        other.accounts[0].unique_id = f"PRIVATE-account-{index}"
        service.accounts.extend(other.accounts)
        service._sessions[service._session_key("kobo", credential)] = other
        sessions.append(other)
    target = sessions[1]
    calls = {s.credential_id: 0 for s in sessions}
    for s in sessions:
        original = s.backup_manifest
        def fetch(s=s, original=original):
            calls[s.credential_id] += 1
            return original()
        s.backup_manifest = fetch
    broker = SynchronizationBrokerImpl(service)
    source_id = broker._source_id(target.accounts[0])
    monkeypatch.setattr(materialize, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")
    monkeypatch.setattr(service, "catalog_scan", lambda **kwargs: pytest.fail("No global scan"))
    monkeypatch.setattr(service, "kobo_adept_state", lambda **kwargs: _ready_adept_state())
    downloaded = []
    def execute(request):
        path = Path(request["destination"])
        downloaded.append(path)
        _write_epub(path)
        return {"ok": True, "operation": "download", "artifact": {
            "path": str(path), "format": "epub", "drm": "none", "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    yield NS(service=service, broker=broker, source=source_id, target=target, sessions=sessions,
             calls=calls, downloaded=downloaded, root=tmp_path)
    service.close()



@pytest.mark.parametrize("contract", ["downloadable_v2", "rights_v1"])
def test_typed_core_delivery_abi_blocks_legacy_confirmation_before_any_io(
    case, monkeypatch, contract,
):
    """A canonical source cannot also acquire via backup_items legacy Core."""
    methods = (
        ("downloadable_resource_snapshot", "downloadable_selected_offer_snapshot",
         "downloadable_selected_delivery_candidate")
        if contract == "downloadable_v2" else
        ("rights_observation_snapshot", "rights_selected_delivery_offer_snapshot",
         "rights_selected_delivery_candidate")
    )
    before = _dump(case)
    for name in methods:
        monkeypatch.setattr(
            case.target, name,
            lambda *_args, **_kwargs: pytest.fail("canonical ABI must not be called here"),
            raising=False,
        )
    blocked = case.broker.prepare_local_materialization(case.source)
    assert blocked.facts["status"] == "canonical_source_delivery_active"
    assert not blocked.ok and "confirmation_token" not in blocked.facts
    assert _dump(case) == before and not case.downloaded
    assert not any(case.calls.values())


@pytest.mark.parametrize("variant", ["legacy", "partial"])
def test_retired_w2_prepare_refuses_before_provider_io(case, monkeypatch, variant):
    """A legacy or partial-ABI Kobo source cannot issue a 0.7 GET permit."""
    if variant == "partial":
        monkeypatch.setattr(
            case.target, "downloadable_resource_snapshot",
            lambda *_args, **_kwargs: pytest.fail("partial ABI must not run"),
            raising=False,
        )
    before = _dump(case)
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok
    assert result.facts["status"] == "canonical_delivery_unavailable"
    assert "confirmation_token" not in result.facts
    assert _dump(case) == before
    assert not any(case.calls.values())
    assert not case.downloaded
    assert not any(s.resolve_calls for s in case.sessions)


@pytest.mark.parametrize("token", ["", "invalid", "a" * 64])
def test_retired_w2_execution_rejects_token_without_io(case, token):
    """Even a shaped token cannot revive the removed 0.7 execution path."""
    before = _dump(case)
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok
    assert result.facts["status"] == "canonical_delivery_unavailable"
    assert _dump(case) == before
    assert not any(case.calls.values())
    assert not case.downloaded


def test_retired_w2_stale_real_plan_token_cannot_dispatch(case):
    """An in-memory legacy plan also grants no permission after cutover."""
    account = case.target.accounts[0]
    scope = (
        case.source, account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    token = case.broker._materialization_plans.put(
        scope, ("legacy manifest",), ("legacy evidence",)
    )
    before = _dump(case)
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok
    assert result.facts["status"] == "canonical_delivery_unavailable"
    assert _dump(case) == before
    assert not any(case.calls.values())
    assert not case.downloaded
