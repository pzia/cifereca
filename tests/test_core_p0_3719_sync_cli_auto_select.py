"""#3719: targeted CLI sync resolves persisted source before network."""
import json
from types import SimpleNamespace

from cifereca import cli


def make_cli_core(monkeypatch, accounts):
    observed = {"connects": 0, "runs": []}
    candidates = [
        SimpleNamespace(
            identity_id=identity, provider_id=provider,
            credential_id=credential, unique_id=account,
        )
        for identity, provider, credential, account, enabled in accounts
    ]
    storage = SimpleNamespace(
        provider_connections=lambda: [
            {"identity_id": identity, "provider_id": provider,
             "credential_id": credential, "enabled": enabled}
            for identity, provider, credential, account, enabled in accounts
        ],
        provider_accounts=lambda: [
            {"identity_id": identity, "provider_id": provider,
             "credential_id": credential, "account_id": account}
            for identity, provider, credential, account, enabled in accounts
        ],
    )
    def connect(*, identity, provider, credential):
        observed["connects"] += 1
        observed["scope"] = (identity, provider, credential)
    def sync(account):
        observed["runs"].append(account)
        return {"status": "success", "downloaded": 1}
    service = SimpleNamespace(
        accounts=candidates, connect_selected_source=connect,
        connect_configured=lambda: (_ for _ in ()).throw(
            AssertionError('global provider connection forbidden')),
        synchronize_account_source=sync,
    )
    core = SimpleNamespace(library=service, storage=storage, close=lambda: None)
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **kw: core)
    return observed


def test_identity_only_unique_run(monkeypatch, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "provider-a", "credential-a", "account-a", True),
        ("profile-b", "provider-b", "credential-b", "account-b", True),
    ])
    assert cli.main(["sync", "run", "--identity", "profile-a", "--confirm"]) == 0
    assert len(observed["runs"]) == observed["connects"] == 1
    assert observed["runs"][0].credential_id == "credential-a"
    assert observed["scope"] == ("profile-a", "provider-a", "credential-a")
    assert json.loads(capsys.readouterr().out)["status"] == "success"


def test_ambiguous_identity_refuses_network(monkeypatch, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "provider-a", "credential-a", "account-a", True),
        ("profile-a", "provider-b", "credential-b", "account-b", True),
    ])
    assert cli.main(["sync", "run", "--identity", "profile-a", "--confirm"]) == 2
    assert observed["connects"] == 0 and not observed["runs"]
    assert json.loads(capsys.readouterr().out)["code"] == "account_ambiguous"


def test_disabled_source_cannot_be_auto_selected(monkeypatch, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "provider-a", "credential-a", "account-a", False),
    ])
    assert cli.main(["sync", "run", "--identity", "profile-a", "--confirm"]) == 2
    assert observed["connects"] == 0 and not observed["runs"]
    assert json.loads(capsys.readouterr().out)["code"] == "source_not_configured"


def test_identity_run_still_needs_explicit_confirmation(monkeypatch, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "provider-a", "credential-a", "account-a", True),
    ])
    assert cli.main(["sync", "run", "--identity", "profile-a"]) == 2
    assert observed["connects"] == 0 and not observed["runs"]
    assert json.loads(capsys.readouterr().out)["code"] == "explicit_run_confirmation_required"



def test_same_provider_multiple_accounts_requires_optional_disambiguation(monkeypatch, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "numilog", "credential-1", "account-1", True),
        ("profile-a", "numilog", "credential-2", "account-2", True),
        ("profile-b", "numilog", "credential-3", "account-3", True),
    ])
    assert cli.main([
        "sync", "run", "--identity", "profile-a",
        "--provider", "numilog", "--confirm",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "account_ambiguous"
    assert observed["connects"] == 0 and observed["runs"] == []
    assert cli.main([
        "sync", "run", "--identity", "profile-a", "--provider", "numilog",
        "--credential", "credential-2", "--confirm",
    ]) == 0
    assert observed["scope"] == ("profile-a", "numilog", "credential-2")
    assert len(observed["runs"]) == 1
    assert observed["runs"][0].identity_id == "profile-a"


def test_unconfigured_provider_is_rejected_without_network(monkeypatch, capsys):
    observed = make_cli_core(monkeypatch, [
        ("profile-a", "other-provider", "credential-1", "account-1", True),
    ])
    assert cli.main([
        "sync", "run", "--identity", "profile-a",
        "--provider", "numilog", "--confirm",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "source_not_configured"
    assert observed["connects"] == 0 and observed["runs"] == []
