"""Retired Paris-specific CLI must fail before Core startup (#5842).

ADEPT multi-account diagnostic is deferred to #5841 (P2). Existing Core
evidence and borrower-binding tests remain independent of the CLI.
"""
from __future__ import annotations

import pytest
from cifereca import cli


@pytest.mark.parametrize("subcommand", [
    "list", "accounts", "audit-origin", "audit-canonical",
    "audit-transaction", "preview-resolution",
    "validate-resolution-snapshot", "bind", "unbind",
])
def test_removed_paris_adept_cli_rejects_before_core_open(
    monkeypatch, capsys, subcommand,
):
    def unexpected_core_open(**_kwargs):
        raise AssertionError("retired CLI cannot open Core")
    monkeypatch.setattr(cli.CiferecaCore, "open", unexpected_core_open)
    with pytest.raises(SystemExit) as exc:
        cli.main(["paris-adept", subcommand])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_removed_paris_adept_not_advertised_in_top_level_help(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "paris-adept" not in capsys.readouterr().out
