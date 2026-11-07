"""#4859: CLI file-only observation never starts Core or mutates legacy rights.

Disposable SQLite/EPUB and a local persisted profile; zero remote I/O.
"""
from __future__ import annotations

import json

import pytest

from cifereca import cli
from cifereca.core import kobo_local_reconciliation as repair
from tests.test_core_p0_4859_kobo_file_only_observation import (
    observation, _snapshot,
)  # noqa: F401


def _invoke(observation, capsys, *, confirm=None, size=None, identity=None):
    case, options = observation
    args = [
        "kobo-observe-local",
        "--credential", options["credential_id"],
        "--identity", identity or options["identity_id"],
        "--account-id", options["account_id"],
        "--item", options["item_id"],
        "--sha256", options["expected_sha256"],
        "--size", str(
            options["expected_size"] if size is None else size,
        ),
        "--path", options["path"],
    ]
    if confirm is not None:
        args.extend(["--confirm", confirm])
    rc = cli.main(args)
    output = capsys.readouterr()
    assert output.err == ""
    return rc, json.loads(output.out)


def test_cli_file_only_preview_confirm_replay_preserves_all_positive_tables(
    observation, monkeypatch, capsys,
):
    case, _ = observation
    case.service.config.save()
    monkeypatch.setattr(repair, "config_file", lambda **_kw: case.service.config.path)
    monkeypatch.setattr(repair, "database_file", lambda **_kw: case.storage.path)
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kw: pytest.fail("file-only CLI must never start Core"),
    )
    before = _snapshot(case)
    rc, preview = _invoke(observation, capsys)
    assert rc == 0 and preview["status"] == "preview"
    assert preview["requires_provider_attestation"] is True
    assert preview["rights_attested"] is False
    assert _snapshot(case) == before
    rc, confirmed = _invoke(
        observation, capsys, confirm=preview["plan_token"],
    )
    assert rc == 0 and confirmed["status"] == "observed"
    assert confirmed["rights_mutations"] == confirmed["legacy_backup_mutations"] == 0
    assert _snapshot(case) == before
    rc, again = _invoke(observation, capsys)
    assert rc == 0 and again["status"] == "already_observed"
    assert _snapshot(case) == before
    public = json.dumps(preview) + json.dumps(confirmed) + json.dumps(again)
    for private in ("PRIVATE", "vault-1", "default", case.sha, str(case.target)):
        assert private not in public


@pytest.mark.parametrize("size,identity,reason", [
    (0, None, "invalid_size"),
    (-1, None, "invalid_size"),
    (None, "wrong-identity", "source_scope_unavailable"),
])
def test_cli_refuses_wrong_scope_or_invalid_size_without_private_echo(
    observation, monkeypatch, capsys, size, identity, reason,
):
    case, options = observation
    case.service.config.save()
    monkeypatch.setattr(repair, "config_file", lambda **_kw: case.service.config.path)
    monkeypatch.setattr(repair, "database_file", lambda **_kw: case.storage.path)
    before = _snapshot(case)
    rc, error = _invoke(
        observation, capsys, size=size, identity=identity,
    )
    assert rc == 2 and error["reason"] == reason
    assert error["ok"] is False and error["changed"] == 0
    assert _snapshot(case) == before
    assert options["credential_id"] not in json.dumps(error)
    assert options["expected_sha256"] not in json.dumps(error)
