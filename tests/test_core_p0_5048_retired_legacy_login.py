"""#5048: remove inert Paris password login from Core and CLI."""
from __future__ import annotations

import ast
import inspect

import pytest

from cifereca import cli
from cifereca import services
from cifereca.core import provider_sessions


def test_core_no_longer_exposes_dead_password_login():
    service_tree = ast.parse(inspect.getsource(services.LibraryService))
    methods = {
        node.name for node in service_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "login" not in methods
    assert not hasattr(provider_sessions, "login")


@pytest.mark.parametrize("argv", [
    ["--username", "reader", "search", "some book"],
    ["--username", "reader", "diagnose"],
    ["--username", "reader", "plugin", "packages"],
])
def test_removed_login_rejects_before_core_start_or_secret_prompt(monkeypatch, argv, capsys):
    def forbidden(*_args, **_kwargs):
        pytest.fail("legacy login must reject before opening Core")
    monkeypatch.setattr(cli.CiferecaCore, "open", forbidden)
    with pytest.raises(SystemExit) as err:
        cli.main(argv)
    assert err.value.code == 2
    stderr = capsys.readouterr().err
    assert "--username est retiré" in stderr
    assert "Mot de passe" not in stderr


def test_legacy_flag_does_not_import_or_request_password():
    tree = ast.parse(inspect.getsource(cli))
    assert not [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Import) and any(a.name == "getpass" for a in node.names)
    ]
