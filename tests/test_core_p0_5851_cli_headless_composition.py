"""P0 #5851: a CLI entry never opts into the GTK-owned built-in UI graph.

The CLI is a Core consumer, not a second desktop. These are real argparse /
command dispatches with fake isolated Core objects; no provider network,
database mutation or GTK toolkit is required.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca import cli


@pytest.mark.parametrize(("args", "expected_output"), [
    (["plugin", "list"], [{"id": "fixture.provider"}]),
    (["catalog-list"], [{"artifact_id": "sha256:fixture"}]),
    (["statistics", "--identity", "profile-a"], [{"identity_id": "profile-a"}]),
])
def test_cli_core_composition_is_headless_and_keeps_command_dispatch(
    monkeypatch, capsys, args, expected_output,
):
    opened, closed, calls = [], [], []
    plugins = NS(diagnostic=lambda: (
        calls.append("plugin-list") or [{"id": "fixture.provider"}]
    ))
    library = NS(
        identities=[NS(identity_id="profile-a"), NS(identity_id="profile-b")],
        catalog_entries=lambda *, state=None: (
            calls.append(("catalog-list", state)) or
            [{"artifact_id": "sha256:fixture"}]
        ),
        identity_statistics=lambda identity_id: (
            calls.append(("statistics", identity_id))
            or {"identity_id": identity_id}
        ),
    )
    core = NS(plugins=plugins, library=library, close=lambda: closed.append(True))

    def open_core(**kwargs):
        opened.append(kwargs)
        return core

    monkeypatch.setattr(cli.CiferecaCore, "open", open_core)
    assert cli.main(args) == 0
    assert opened == [{
        "diagnostics_enabled": True,
        "diagnostics_level": "detailed",
        "builtin_ui": False,
    }]
    assert closed == [True]
    assert json.loads(capsys.readouterr().out) == expected_output
    if args[0] == "plugin":
        assert calls == ["plugin-list"]
    elif args[0] == "catalog-list":
        assert calls == [("catalog-list", None)]
    else:
        assert calls == [("statistics", "profile-a")]


def test_unconfirmed_cli_source_action_still_preflights_before_core_or_plugins(
    monkeypatch, capsys,
):
    opened = []
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kwargs: opened.append(kwargs),
    )
    assert cli.main([
        "sync", "run", "--provider", "paris", "--identity", "profile-a",
    ]) == 2
    assert opened == []
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "code": "explicit_run_confirmation_required",
    }


def test_headless_cli_mode_never_changes_gui_composition_default(tmp_path):
    from cifereca.core import CiferecaCore

    # The GTK process keeps its established builtin_ui=True default.
    # A direct Core consumer can explicitly opt out without changing this.
    with CiferecaCore.open(
        config_path=tmp_path / "config.json",
        database_path=tmp_path / "library.sqlite",
        builtin_ui=False,
        activate_plugins=False,
    ) as core:
        assert core.plugins._builtin_ui is False
        core.plugins.refresh()
        assert not any(
            record.package.sha256 == "builtin"
            for record in core.plugins._records.values()
        )
