"""P1 #4407: named Kobo identities cannot spuriously invalidate a read-only preview.

All provider responses are synthetic. No real network or acquisition.
"""
from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca import cli
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_fault_isolation import _service


MANIFEST = [{"product_id": "synthetic-epub", "revision_id": "r1",
             "format": "EPUB", "downloadable": True}]


def _named(service, name: str = "heloise"):
    identity = replace(service.identities[0], identity_id=name,
                       label=name.title(), directory_name=name)
    service.identities.append(identity)
    service.storage.identity_put(identity)
    service.storage.provider_connection_put({
        "identity_id": name, "provider_id": "kobo",
        "credential_id": "vault-1", "label": "Kobo explicit",
    })



@pytest.mark.parametrize("named", [False, True])
def test_partial_abi_named_owner_never_fetches_legacy_manifest(tmp_path, named):
    service, session = _service(tmp_path, MANIFEST)
    if named:
        _named(service)
    calls = []
    session.backup_manifest = lambda: calls.append("manifest") or MANIFEST
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == []
        assert service.storage.backup_list("kobo", "vault-1") == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()


def test_foreign_identity_never_grants_legacy_preview(tmp_path):
    service, session = _service(tmp_path, MANIFEST)
    _named(service)
    session.accounts[0].identity_id = "another-identity"
    calls = []
    session.backup_manifest = lambda: calls.append("manifest") or MANIFEST
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    "binding_reassigned", "connected_false", "session_replaced",
    "account_replaced", "account_renamed", "core_epoch",
])
def test_named_owner_cannot_race_after_disallowed_manifest_read(tmp_path, change):
    service, session = _service(tmp_path, MANIFEST)
    _named(service)
    calls = []
    session.backup_manifest = lambda: calls.append("manifest") or MANIFEST
    try:
        # The old D1 provider fetch is forbidden *before* a rebind race.
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == []
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()


def test_named_owner_disconnected_diagnostic_requires_exact_binding(tmp_path):
    service, session = _service(tmp_path, MANIFEST)
    _named(service)
    session.connected = False
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_scope_unavailable"):
            service.kobo_backup_preview("vault-1")
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
    finally:
        service.close()


@pytest.mark.parametrize("stage", ["connect", "manifest"])
def test_cli_refreshed_preview_errors_return_stable_json_and_nonzero(
    monkeypatch, capsys, stage,
):
    closed = []
    def unavailable(*args, **kwargs):
        raise ProviderConfigurationError(
            "Kobo source changed /home/private/credentials?token=SECRET"
        )
    service = SimpleNamespace(
        connect_kobo_canonical=unavailable if stage == "connect" else lambda x: None,
        kobo_backup_preview=unavailable,
    )
    core = SimpleNamespace(library=service, close=lambda: closed.append(True))
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **kwargs: core)
    status = cli.main([
        "kobo-resume-preview", "--credential", "kobo-default",
        "--refresh-manifest",
    ])
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert status == 2
    assert result == {
        "status": "unavailable", "code": "kobo_preview_unavailable",
        "preview_only": True, "acquisition_authorized": False,
    }
    assert "SECRET" not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert closed == [True]


def test_cli_offline_preview_never_connects_provider(monkeypatch, capsys):
    closed = []
    def no_connect(_credential):
        raise AssertionError("offline preview must not connect")
    service = SimpleNamespace(
        connect_kobo_preview=no_connect,
        connect_kobo_canonical=no_connect,
        kobo_backup_preview=lambda *_a, **kw: {
            "preview_only": True, "manifest_refreshed": False,
            "acquisition_authorized": False,
        },
    )
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kwargs: SimpleNamespace(library=service,
                                         close=lambda: closed.append(True)),
    )
    assert cli.main(["kobo-resume-preview", "--credential", "kobo-default"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["manifest_refreshed"] is False
    assert result["acquisition_authorized"] is False
    assert closed == [True]


def test_cli_named_identity_partial_abi_returns_redacted_refusal(
    tmp_path, monkeypatch, capsys,
):
    from cifereca.core import provider_sessions
    service, session = _service(tmp_path, MANIFEST)
    _named(service, "heloise")
    called = []
    session.backup_manifest = lambda: pytest.fail("no old manifest GET")
    def connect_target(owner, credential_id):
        assert owner is service and credential_id == "vault-1"
        called.append("connect exact")
        return session
    monkeypatch.setattr(provider_sessions, "connect_kobo_preview", connect_target)
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kwargs: SimpleNamespace(library=service, close=lambda: None),
    )
    try:
        assert cli.main([
            "kobo-resume-preview", "--credential", "vault-1",
            "--refresh-manifest",
        ]) == 2
        output = capsys.readouterr()
        result = json.loads(output.out)
        assert called == ["connect exact"]
        assert result["status"] == "unavailable"
        assert result["acquisition_authorized"] is False
        assert result["preview_only"] is True
        assert "Traceback" not in output.out + output.err
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()
