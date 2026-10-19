"""P0 #1143: failed identity-directory --abort prints no private storage error."""
from __future__ import annotations

import json

import pytest

from cifereca import cli


SECRET = "/home/private-reader/EBooks/account-SIGNED_TOKEN/library.sqlite3"


def test_abort_directory_cli_redacts_filesystem_and_database_error(monkeypatch, capsys):
    def fail():
        raise OSError(SECRET)

    monkeypatch.setattr(cli.CiferecaCore, "abort_directory_migration", fail)
    assert cli.main(["identity-directory", "--abort"]) == 1
    printed = capsys.readouterr()
    assert printed.out == ""
    assert printed.err.strip() == "Annulation de migration impossible"
    assert "SIGNED_TOKEN" not in printed.err
    assert "/home/private-reader" not in printed.err


def test_abort_directory_cli_never_stringifies_hostile_error(monkeypatch, capsys):
    class Hostile(RuntimeError):
        def __str__(self):
            raise AssertionError("Must not stringify private migration error")

    monkeypatch.setattr(
        cli.CiferecaCore, "abort_directory_migration",
        lambda: (_ for _ in ()).throw(Hostile(SECRET)),
    )
    assert cli.main(["identity-directory", "--abort"]) == 1
    assert capsys.readouterr().err.strip() == "Annulation de migration impossible"


def test_successful_migration_abort_still_returns_original_json(monkeypatch, capsys):
    result = {"ok": True, "originals_preserved": True, "resumed": False}
    monkeypatch.setattr(cli.CiferecaCore, "abort_directory_migration", lambda: result)
    assert cli.main(["identity-directory", "--abort"]) == 0
    printed = capsys.readouterr()
    assert json.loads(printed.out) == result
    assert printed.err == ""
