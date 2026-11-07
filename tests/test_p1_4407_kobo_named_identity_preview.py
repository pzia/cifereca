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
def test_refresh_accepts_plugin_default_identity_only_with_exact_core_binding(
    tmp_path, named,
):
    service, session = _service(tmp_path, MANIFEST)
    if named:
        _named(service)
    calls = []
    original = session.backup_manifest

    def manifest():
        calls.append("manifest")
        return original()

    session.backup_manifest = manifest
    try:
        assert session.accounts[0].identity_id == "default"
        result = service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == ["manifest"]
        assert result["preview_only"] is True
        assert result["acquisition_authorized"] is False
        assert result["manifest_refreshed"] is True
        assert result["manifest_items"] == 1
        assert service.storage.backup_list("kobo", "vault-1") == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()


def test_foreign_nondefault_account_identity_rejected_before_manifest(tmp_path):
    service, session = _service(tmp_path, MANIFEST)
    _named(service)
    session.accounts[0].identity_id = "another-identity"
    calls = []
    session.backup_manifest = lambda: calls.append("manifest") or MANIFEST
    try:
        with pytest.raises(ProviderConfigurationError, match="Source Kobo modifiée"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    "binding_reassigned", "connected_false", "session_replaced",
    "account_replaced", "account_renamed", "core_epoch",
])
def test_named_identity_preserves_freshness_guard_after_provider_get(
    tmp_path, change,
):
    service, session = _service(tmp_path, MANIFEST)
    _named(service)
    previous = session.backup_manifest
    calls = []

    def raced():
        data = previous()
        calls.append("manifest")
        if change == "binding_reassigned":
            other = replace(service.identities[0], identity_id="other",
                            label="Other", directory_name="other")
            service.identities.append(other)
            service.storage.identity_put(other)
            service.storage.provider_connection_put({
                "identity_id": "other", "provider_id": "kobo",
                "credential_id": "vault-1", "label": "Rebound",
            })
        elif change == "connected_false":
            session.connected = False
        elif change == "session_replaced":
            service._sessions[service._session_key("kobo", "vault-1")] = object()
        elif change == "account_replaced":
            session._accounts = [replace(session.accounts[0])]
        elif change == "account_renamed":
            session.accounts[0].unique_id = "different-upstream-account"
        else:
            service._source_context_epoch += 1
        return data

    session.backup_manifest = raced
    try:
        with pytest.raises(ProviderConfigurationError, match="Source Kobo modifiée"):
            service.kobo_backup_preview("vault-1", refresh_manifest=True)
        assert calls == ["manifest"]
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()


def test_named_identity_disconnected_offline_preview_remains_readonly(tmp_path):
    service, session = _service(tmp_path, MANIFEST)
    _named(service)
    session.connected = False
    try:
        result = service.kobo_backup_preview("vault-1")
        assert result["manifest_refreshed"] is False
        assert result["preview_only"] is True
        assert result["acquisition_authorized"] is False
        with pytest.raises(ProviderConfigurationError, match="non connecté"):
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


def test_cli_named_identity_refresh_reaches_provider_manifest_without_traceback(
    tmp_path, monkeypatch, capsys,
):
    from cifereca.core import provider_sessions

    service, session = _service(tmp_path, MANIFEST)
    _named(service, "heloise")
    called = []
    original = session.backup_manifest

    def manifest():
        called.append("GET manifest")
        return original()

    def connect_target(owner, credential_id):
        assert owner is service and credential_id == "vault-1"
        called.append("connect exact")
        return session

    session.backup_manifest = manifest
    monkeypatch.setattr(provider_sessions, "connect_kobo_preview", connect_target)
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kwargs: SimpleNamespace(library=service, close=lambda: None),
    )
    try:
        assert cli.main([
            "kobo-resume-preview", "--credential", "vault-1",
            "--refresh-manifest",
        ]) == 0
        output = capsys.readouterr()
        result = json.loads(output.out)
        assert called == ["connect exact", "GET manifest"]
        assert result["manifest_refreshed"] is True
        assert result["preview_only"] is True
        assert result["acquisition_authorized"] is False
        assert "Traceback" not in output.out + output.err
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()
