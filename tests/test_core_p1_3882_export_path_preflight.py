"""#3882: reject known-impossible export paths before any source login.

All arguments are synthetic; no library, provider connection or filesystem
mutation is allowed from the CLI before a valid destination is selected.
"""
from __future__ import annotations

import json

import pytest

from cifereca import cli


def _cli_args(command, path):
    if command == "audit":
        return [
            "acquisition-audit", "--identity", "profile-a",
            "--provider", "numilog", "--output", str(path),
        ]
    args = [
        "sync", "run", "--identity", "profile-a",
        "--provider", "numilog", "--confirm",
    ]
    if command == "report":
        return [*args, "--report", str(path)]
    return [*args, "--trace-export", str(path), "--confirm-trace-export"]


@pytest.mark.parametrize(("command", "reason"), [
    ("audit", "audit_destination_unavailable"),
    ("report", "report_destination_unavailable"),
    ("trace", "trace_export_destination_unavailable"),
])
@pytest.mark.parametrize("unsafe", ["missing_parent", "symlink_ancestor"])
def test_unsafe_output_preflight_is_offline(
    monkeypatch, tmp_path, capsys, command, reason, unsafe,
):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Core or provider must not start")
        ),
    )
    if unsafe == "missing_parent":
        destination = tmp_path / "nonexistent" / "audit.zip"
    else:
        actual = tmp_path / "actual"
        actual.mkdir()
        alias = tmp_path / "symlink"
        alias.symlink_to(actual, target_is_directory=True)
        destination = alias / "audit.zip"
    assert cli.main(_cli_args(command, destination)) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "code": reason,
    }
    assert not destination.exists()
    assert not destination.is_symlink()


@pytest.mark.parametrize(("command", "reason"), [
    ("audit", "audit_destination_exists"),
    ("report", "report_destination_exists"),
    ("trace", "trace_export_destination_exists"),
])
def test_existing_path_keeps_prior_closed_error_precedence(
    monkeypatch, tmp_path, capsys, command, reason,
):
    destination = tmp_path / "already.zip"
    destination.write_bytes(b"KEEP")
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must be handled before Core")
        ),
    )
    assert cli.main(_cli_args(command, destination)) == 2
    assert json.loads(capsys.readouterr().out)["code"] == reason
    assert destination.read_bytes() == b"KEEP"


def test_valid_parent_not_rejected_and_preflight_is_read_only(tmp_path):
    destination = tmp_path / "new.zip"
    assert cli._export_parent_is_safe(destination) is True
    assert not destination.exists()


def test_symlinked_direct_destination_cannot_pass_preflight(tmp_path):
    actual = tmp_path / "actual.zip"
    actual.write_bytes(b"KEEP")
    alias = tmp_path / "linked.zip"
    alias.symlink_to(actual)
    assert cli._export_parent_is_safe(alias) is False
    assert actual.read_bytes() == b"KEEP"
