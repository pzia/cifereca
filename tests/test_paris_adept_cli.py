"""CLI contract for explicitly confirmed Paris borrower ADEPT bindings (#976)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca import cli
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def stub_core(monkeypatch, service):
    core = SimpleNamespace(
        library=service, storage=service.storage, close=lambda: None,
    )
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **kwargs: core)
    return core


def test_bind_requires_confirmation_before_opening_database(monkeypatch, capsys):
    def forbidden(**kwargs):
        raise AssertionError("Core should not be opened without confirmation")
    monkeypatch.setattr(cli.CiferecaCore, "open", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main([
            "paris-adept", "bind", "--credential", "alice-paris",
            "--borrower", "linked-123", "--identity", "bob",
        ])
    assert error.value.code == 2
    assert "--confirm-ownership" in capsys.readouterr().err


def test_explicit_bind_and_offline_list_do_not_reconnect(tmp_path, monkeypatch, capsys):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        stub_core(monkeypatch, service)
        seen = []
        monkeypatch.setattr(service, "connect_configured", lambda: seen.append("connected"))
        assert cli.main([
            "paris-adept", "bind", "--credential", "alice-paris",
            "--borrower", "linked-123", "--identity", "bob",
            "--confirm-ownership",
        ]) == 0
        bound = json.loads(capsys.readouterr().out)
        assert bound["borrower_identity_id"] == "bob"
        assert bound["connection_identity_id"] == "alice"
        assert seen == ["connected"]

        assert cli.main(["paris-adept", "list", "--credential", "alice-paris"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert len(report["bindings"]) == 1
        assert report["bindings"][0]["configuration_consistent"] is True
        assert report["bindings"][0]["profile_id"] == "bob-profile"
        assert seen == ["connected"]  # no remote login for read-only list
    finally:
        service.close()


def test_cli_revoke_does_not_contact_provider(tmp_path, monkeypatch, capsys):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        stub_core(monkeypatch, service)
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )

        def forbidden():
            raise AssertionError("Revocation must remain offline")
        monkeypatch.setattr(service, "connect_configured", forbidden)
        assert cli.main([
            "paris-adept", "unbind",
            "--credential", "alice-paris", "--borrower", "linked-123",
        ]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "revoked"
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123"
        ) is None
    finally:
        service.close()


def test_cli_refuses_unknown_linked_borrower_without_creating_grant(
    tmp_path, monkeypatch, capsys,
):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        stub_core(monkeypatch, service)
        monkeypatch.setattr(service, "connect_configured", lambda: None)
        assert cli.main([
            "paris-adept", "bind", "--credential", "alice-paris",
            "--borrower", "unknown", "--identity", "bob",
            "--confirm-ownership",
        ]) == 2
        assert "not uniquely identified" in capsys.readouterr().err
        assert service.storage.paris_borrower_adept_bindings() == []
    finally:
        service.close()


def test_cli_accounts_displays_connection_scoped_borrowers(
    tmp_path, monkeypatch, capsys,
):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        stub_core(monkeypatch, service)
        seen = []
        monkeypatch.setattr(service, "connect_configured", lambda: seen.append("connected"))
        assert cli.main(["paris-adept", "accounts"]) == 0
        rows = json.loads(capsys.readouterr().out)
        assert seen == ["connected"]
        assert {row["account_id"] for row in rows} == {"", "linked-123"}
        assert all(row["credential_id"] == "alice-paris" for row in rows)
    finally:
        service.close()


def test_bindings_list_flags_stale_profile_without_mutation(
    tmp_path, monkeypatch, capsys,
):
    service, _ = service_with_linked_borrower(tmp_path)
    try:
        stub_core(monkeypatch, service)
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.storage.adept_profile_binding_set("identity", "bob", "changed")
        assert cli.main(["paris-adept", "list"]) == 0
        rows = json.loads(capsys.readouterr().out)["bindings"]
        assert len(rows) == 1
        assert rows[0]["configuration_consistent"] is False
        assert rows[0]["profile_id"] == "bob-profile"
    finally:
        service.close()
