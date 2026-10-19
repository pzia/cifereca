"""P0 #2290: closed smoke diagnostics survive real CLI/process contracts.

Only synthetic plugin inventories/temporary paths; no GitHub, login, real
plugin installation or personal data. Original smoke rejection is preserved.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace as NS

import pytest

from cifereca import dev_updater, updater
from cifereca.update_progress import (
    SMOKE_FAILURE_CODES, child_progress_receiver, update_operation, update_phase,
)

PRIVATE = "https://private.invalid/reader?auth=PRIVATE_PLUGIN_SMOKE_2290"
ZERO = {"contributions_error": 0, "contributions_incompatible": 0,
        "active_packages_missing": 0}


def _inventory(monkeypatch, rows, *, returncode=0, package_active=None):
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: "/fake/runtime/cli")
    monkeypatch.setattr(
        updater, "_run_bounded_process",
        lambda *_a, **_kw: NS(returncode=returncode, stdout=json.dumps(rows), stderr=PRIVATE),
    )
    if package_active is not None:
        monkeypatch.setattr(dev_updater, "_package_is_active", package_active)


@pytest.mark.parametrize(("rows", "exit_code", "cause", "counts"), [
    ([{"plugin_id": PRIVATE, "package_id": "org.demo", "state": "error"}],
     0, "smoke_contributions_failed",
     {**ZERO, "contributions_error": 1}),
    ([{"plugin_id": PRIVATE, "package_id": "org.demo", "state": "incompatible"}],
     0, "smoke_contributions_failed",
     {**ZERO, "contributions_incompatible": 1}),
    ([{"plugin_id": PRIVATE, "package_id": "org.demo", "state": "ready"}],
     4, "smoke_cli_failed", ZERO),
    ({"html": PRIVATE}, 0, "smoke_inventory_invalid", ZERO),
    ([{"plugin_id": PRIVATE, "package_id": "org.demo", "state": []}],
     0, "smoke_inventory_invalid", ZERO),
])
def test_smoke_failure_reports_only_closed_code_and_counts(
    monkeypatch, rows, exit_code, cause, counts,
):
    _inventory(monkeypatch, rows, returncode=exit_code)
    events, log = [], io.StringIO()
    with pytest.raises(dev_updater.PluginSmokeError) as failure:
        with update_operation("dev_snapshot", callback=events.append, stream=log):
            with update_phase("smoke"):
                dev_updater._smoke_plugins(["org.demo"])
    assert failure.value.reason_code == cause
    assert failure.value.smoke_counts == counts
    assert PRIVATE not in str(failure.value) + repr(events) + log.getvalue()
    smoke = [e for e in events if e["stage"] == "smoke" and e["status"] == "ERROR"]
    assert len(smoke) == 1
    assert smoke[0]["reason_code"] == cause
    assert smoke[0]["smoke_counts"] == counts
    assert f"reason={cause}" in log.getvalue()
    assert events[-1]["outcome"] == "failed"


def test_active_missing_count_excludes_intentionally_disabled_packages(monkeypatch):
    rows = [{"plugin_id": "healthy", "package_id": "active-visible", "state": "ready"}]
    _inventory(monkeypatch, rows, package_active=lambda name: name != "disabled")
    with pytest.raises(dev_updater.PluginSmokeError) as failure:
        dev_updater._smoke_plugins(
            ["active-visible", "active-absent", "disabled"],
        )
    assert failure.value.reason_code == "smoke_active_missing"
    assert failure.value.smoke_counts == {**ZERO, "active_packages_missing": 1}
    assert "active-absent" not in str(failure.value)
    assert "disabled" not in str(failure.value)
    assert dev_updater._smoke_plugins(["active-visible", "disabled"]) == {"plugin_count": 1}


def test_unrelated_errors_do_not_fail_selected_snapshot(monkeypatch):
    _inventory(monkeypatch, [
        {"plugin_id": PRIVATE, "package_id": "unrelated", "state": "error"},
        {"plugin_id": "selected", "package_id": "org.demo", "state": "ready"},
    ])
    assert dev_updater._smoke_plugins(["org.demo"]) == {"plugin_count": 2}


def test_dev_cli_error_json_includes_only_closed_evidence(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise dev_updater.PluginSmokeError(
            "smoke plugins: contributions installées en erreur",
            reason_code="smoke_contributions_failed", contributions_error=2,
            contributions_incompatible=1,
        )
    monkeypatch.setattr(dev_updater, "apply_snapshot", fail)
    assert dev_updater.main(["synthetic.zip", "--json"]) == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report["error"]["code"] == "dev_update_error"
    assert report["error"]["reason_code"] == "smoke_contributions_failed"
    assert report["error"]["smoke_counts"] == {
        **ZERO, "contributions_error": 2, "contributions_incompatible": 1,
    }
    assert PRIVATE not in output.out + output.err


def _child(monkeypatch, tmp_path, payload):
    command = tmp_path / "cifereca-update-dev"
    command.touch()
    monkeypatch.setattr(updater, "_command_path", lambda *_a, **_kw: command)
    monkeypatch.setattr(
        updater, "run_plugin_child",
        lambda *_a, **_kw: NS(returncode=1, stdout=payload, stderr=PRIVATE),
    )


def test_release_parent_propagates_only_verified_child_evidence(monkeypatch, tmp_path, capsys):
    cause = "smoke_contributions_failed"
    counts = {**ZERO, "contributions_error": 3, "contributions_incompatible": 2}
    payload = json.dumps({
        "ok": False,
        "error": {"code": "dev_update_error", "reason_code": cause,
                  "smoke_counts": counts, "message": PRIVATE,
                  "package_id": PRIVATE, "stderr": PRIVATE},
    })
    _child(monkeypatch, tmp_path, payload)
    def install(*_args, **_kw):
        with update_phase("plugin_activation"):
            return updater._apply_release_plugin_snapshot(tmp_path, tmp_path / "snapshot")
    monkeypatch.setattr(updater, "install_github", install)
    assert updater.main(["--github", "main", "--json"]) == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["error"] == {
        "code": "update_error", "message": "Upgrade des plugins de release échoué",
        "reason_code": cause, "smoke_counts": counts,
    }
    assert PRIVATE not in captured.out + captured.err
    assert "reason=smoke_contributions_failed" in captured.err
    assert "smoke_errors=3" in captured.err
    assert "smoke_incompatible=2" in captured.err


@pytest.mark.parametrize("child_value", [
    PRIVATE,
    json.dumps({"ok": False, "error": {
        "code": "dev_update_error", "reason_code": PRIVATE,
        "smoke_counts": ZERO,
    }}),
    json.dumps({"ok": False, "error": {
        "code": "dev_update_error", "reason_code": "smoke_active_missing",
        "smoke_counts": {**ZERO, "active_packages_missing": True},
    }}),
    json.dumps({"ok": False, "error": {
        "code": "dev_update_error", "reason_code": "smoke_active_missing",
        "smoke_counts": {**ZERO, "extra_private": PRIVATE},
    }}),
])
def test_malformed_child_error_cannot_be_promoted(monkeypatch, tmp_path, child_value):
    _child(monkeypatch, tmp_path, child_value)
    with pytest.raises(updater.UpdateError) as failure:
        updater._apply_release_plugin_snapshot(tmp_path, tmp_path / "snapshot")
    assert type(failure.value) is updater.UpdateError
    assert str(failure.value) == "Upgrade des plugins de release échoué"
    assert PRIVATE not in str(failure.value)


def test_parent_child_progress_only_admits_closed_smoke_fields():
    events = []
    with update_operation("github", callback=events.append):
        receive = child_progress_receiver()
        receive(json.dumps({
            "schema": "cifereca.update.progress/1", "stage": "smoke",
            "status": "ERROR", "reason_code": "smoke_active_missing",
            "smoke_counts": {**ZERO, "active_packages_missing": 2},
            "plugin_id": PRIVATE, "message": PRIVATE, "transaction_ref": PRIVATE,
        }).encode())
        receive(json.dumps({
            "schema": "cifereca.update.progress/1", "stage": "smoke",
            "status": "ERROR", "reason_code": "smoke_active_missing",
            "smoke_counts": {**ZERO, "contributions_error": True},
        }).encode())
    smoke = [e for e in events if e["stage"] == "smoke"]
    assert len(smoke) == 2
    assert smoke[0]["smoke_counts"] == {**ZERO, "active_packages_missing": 2}
    assert "smoke_counts" not in smoke[1]
    assert PRIVATE not in repr(events)


def test_closed_reason_catalogue_is_exact():
    assert SMOKE_FAILURE_CODES == {
        "smoke_cli_failed", "smoke_json_invalid", "smoke_inventory_invalid",
        "smoke_contributions_failed", "smoke_active_missing",
    }
