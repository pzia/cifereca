"""P0 #2290: a failed contribution is attributable before transactional rollback.

Only synthetic manifest archives and test doubles. Never inspect user configs,
raw child process logs, private URLs or a live plugin environment.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import dev_updater, updater
from cifereca.plugins import packages


PRIVATE = "https://private.invalid/signed.acsm?auth=DO_NOT_PUBLISH"
ZERO = {"contributions_error": 1, "contributions_incompatible": 0,
        "active_packages_missing": 0}
PACKAGE = "org.cifereca.provider.synthetic"
PLUGIN = "provider.synthetic"


def _snapshot(path: Path) -> Path:
    package_bytes = io.BytesIO()
    descriptor = {
        "manifest": 1,
        "package": {"id": PACKAGE, "version": "1.0.0"},
        "plugins": [{"id": PLUGIN, "type": "provider.catalog",
                     "entrypoint": "test:Provider"}],
    }
    with zipfile.ZipFile(package_bytes, "w") as embedded:
        embedded.writestr("cifereca-plugin.json", json.dumps(descriptor))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("release/cifereca-dev.json", json.dumps({
            "format": 1, "kind": "cifereca-dev-snapshot",
            "plugins": [{"path": "packages/demo.zip"}],
        }))
        archive.writestr("release/packages/demo.zip", package_bytes.getvalue())
    return path


def _rows(*, plugin_id=PLUGIN, reason_code="activation_failed"):
    return [
        {"package_id": PACKAGE, "plugin_id": plugin_id, "state": "error",
         "reason_code": reason_code, "error": PRIVATE, "display_name": PRIVATE},
    ]


def _fake_manifest_store(monkeypatch):
    class Store:
        def __init__(self, _root): pass
        def active_version(self, package_id):
            return "1.0.0" if package_id == PACKAGE else None
        def load(self, _package_id):
            return NS(manifest=NS(
                package=NS(id=PACKAGE), plugins=[NS(id=PLUGIN)],
            ))
    monkeypatch.setattr(packages, "PackageStore", Store)


@pytest.mark.parametrize("plugin_id,reason,expected", [
    (PLUGIN, "activation_failed", "activation_failed"),
    (PLUGIN, PRIVATE, "unknown"),
    (PRIVATE, "activation_failed", None),
    ("provider.unrelated", "activation_failed", None),
])
def test_child_smoke_carries_only_installed_manifest_declared_plugin(
    monkeypatch, plugin_id, reason, expected,
):
    _fake_manifest_store(monkeypatch)
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: "/synthetic/cli")
    monkeypatch.setattr(
        updater, "_run_bounded_process",
        lambda *_a, **_kw: NS(returncode=0, stdout=json.dumps(
            _rows(plugin_id=plugin_id, reason_code=reason),
        )),
    )
    with pytest.raises(dev_updater.PluginSmokeError) as caught:
        dev_updater._smoke_plugins([PACKAGE])
    exc = caught.value
    assert exc.reason_code == "smoke_contributions_failed"
    assert exc.smoke_counts == ZERO
    if expected is None:
        assert exc.failed_contributions == []
    else:
        assert exc.failed_contributions == [{
            "package_id": PACKAGE, "plugin_id": PLUGIN, "reason_code": expected,
        }]
    assert PRIVATE not in str(exc) + repr(exc.failed_contributions)


def test_parent_attests_child_against_selected_zip_before_exposing_identity(tmp_path):
    snapshot = _snapshot(tmp_path / "release-plugins.zip")
    good = [{"package_id": PACKAGE, "plugin_id": PLUGIN,
             "reason_code": "activation_failed"}]
    assert updater._attested_snapshot_smoke_failures(snapshot, good) == good
    for attempted in [
        [{"package_id": PACKAGE, "plugin_id": PRIVATE,
          "reason_code": "activation_failed"}],
        [{"package_id": PACKAGE, "plugin_id": PLUGIN,
          "reason_code": PRIVATE}],
        [{"package_id": PRIVATE, "plugin_id": PLUGIN,
          "reason_code": "activation_failed"}],
        [*good, *good],
        [{"package_id": PACKAGE, "plugin_id": PLUGIN,
          "reason_code": "activation_failed", "private": PRIVATE}],
    ]:
        assert updater._attested_snapshot_smoke_failures(snapshot, attempted) == []


@pytest.mark.parametrize("trusted", [True, False])
def test_parent_error_json_survives_rollback_boundary_without_raw_child_text(
    tmp_path, monkeypatch, capsys, trusted,
):
    snapshot = _snapshot(tmp_path / "release-plugins.zip")
    launcher = tmp_path / "cifereca-update-dev"
    launcher.write_text("synthetic")
    monkeypatch.setattr(updater, "_command_path", lambda *_a: launcher)
    candidate = [{"package_id": PACKAGE, "plugin_id": (
        PLUGIN if trusted else PRIVATE), "reason_code": "activation_failed"}]
    monkeypatch.setattr(updater, "run_plugin_child", lambda *_a, **_kw: NS(
        returncode=1, stdout=json.dumps({
            "ok": False, "error": {
                "code": "dev_update_error",
                "message": PRIVATE,
                "reason_code": "smoke_contributions_failed",
                "smoke_counts": ZERO,
                "failed_contributions": candidate,
                "stderr": PRIVATE,
            },
        }),
    ))
    monkeypatch.setattr(updater, "install_github", lambda *_a, **_kw:
                        updater._apply_release_plugin_snapshot(tmp_path, snapshot))
    assert updater.main(["--github", "main", "--json"]) == 1
    output = capsys.readouterr()
    error = json.loads(output.out)["error"]
    assert error["message"] == "Upgrade des plugins de release échoué"
    assert error["reason_code"] == "smoke_contributions_failed"
    assert error["smoke_counts"] == ZERO
    if trusted:
        assert error["failed_contributions"] == candidate
    else:
        assert "failed_contributions" not in error
    assert PRIVATE not in output.out + output.err


def test_legacy_child_failure_without_attribution_keeps_original_counts(
    tmp_path, monkeypatch,
):
    snapshot = _snapshot(tmp_path / "release-plugins.zip")
    launcher = tmp_path / "cifereca-update-dev"
    launcher.write_text("synthetic")
    monkeypatch.setattr(updater, "_command_path", lambda *_a: launcher)
    monkeypatch.setattr(updater, "run_plugin_child", lambda *_a, **_kw: NS(
        returncode=1, stdout=json.dumps({
            "ok": False, "error": {
                "code": "dev_update_error",
                "reason_code": "smoke_contributions_failed",
                "smoke_counts": ZERO,
            },
        }),
    ))
    with pytest.raises(updater.ReleasePluginSmokeError) as caught:
        updater._apply_release_plugin_snapshot(tmp_path, snapshot)
    assert caught.value.smoke_counts == ZERO
    assert caught.value.failed_contributions == []
