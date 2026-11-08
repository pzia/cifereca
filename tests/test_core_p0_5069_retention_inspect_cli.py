"""P0 #5069: official retention inspect CLI is read-only and exclusive."""
from __future__ import annotations

import json

import pytest

from cifereca import updater


def test_retention_inspect_json_on_absent_stores_is_pure(tmp_path, monkeypatch, capsys):
    runtime = tmp_path / "runtime"
    packages = tmp_path / "plugins"
    monkeypatch.setattr(updater, "install_root", lambda: runtime)
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: packages)
    monkeypatch.setattr(updater, "state_dir", lambda **_kwargs: tmp_path / "state")
    assert updater.main(["--retention-inspect", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "cifereca.updater.retention-inventory-combined/1"
    assert report["safe_to_apply"] is False
    assert "cross_store_reference_proofs" in report["pending_stores"]
    assert report["plans"]["core_transactions"] is not None
    assert report["plans"]["plugin_versions"] is not None
    assert not runtime.exists()
    assert not packages.exists()


@pytest.mark.parametrize("forbidden", [
    ["--rollback"],
    ["--status"],
    ["--github", "main"],
    ["--recovery-inspect"],
    ["--recovery-reconcile", "20261108T101214Z-2b88f133f09d"],
    ["--acknowledge", "a" * 64],
    ["--dry-run"],
    ["--to", "previous"],
])
def test_retention_inspect_rejects_mutating_and_other_modes(
    tmp_path, monkeypatch, forbidden,
):
    root = tmp_path / "runtime"
    monkeypatch.setattr(updater, "install_root", lambda: root)
    monkeypatch.setattr(updater, "state_dir", lambda **_kwargs: tmp_path / "state")
    monkeypatch.setattr(updater, "rollback", lambda *args: pytest.fail("rollback called"))
    monkeypatch.setattr(updater, "install_github", lambda *args, **kw: pytest.fail("github called"))
    monkeypatch.setattr(updater, "reconcile_lost_previous", lambda *args, **kw: pytest.fail("reconcile called"))
    with pytest.raises(SystemExit) as raised:
        updater.main(["--retention-inspect", *forbidden, "--json"])
    assert raised.value.code == 2
    assert not root.exists()


def test_retention_inspect_reports_plugin_versions_without_package_names(
    tmp_path, monkeypatch, capsys,
):
    from datetime import datetime, timezone

    runtime = tmp_path / "runtime"
    packages = tmp_path / "plugins"
    private_id = "org.private.secret-provider"
    v = packages / private_id / "1.0.0"
    v.mkdir(parents=True)
    (v / "cifereca-plugin.json").write_text(json.dumps({
        "package": {"id": private_id, "version": "1.0.0"},
    }), encoding="utf-8")
    (v / ".cifereca-install.json").write_text(json.dumps({
        "sha256": "a" * 64,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    (v.parent / "current.json").write_text(json.dumps({
        "active": "1.0.0", "previous": None,
    }), encoding="utf-8")

    monkeypatch.setattr(updater, "install_root", lambda: runtime)
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: packages)
    monkeypatch.setattr(updater, "state_dir", lambda **_kwargs: tmp_path / "state")
    assert updater.main(["--retention-inspect", "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert private_id not in output
    assert str(tmp_path) not in output
    assert report["plans"]["plugin_versions"]["total"] == 1
    assert report["plans"]["plugin_versions"]["candidates"] == 0
    assert report["safe_to_apply"] is False



def test_cli_includes_qualified_dev_backups_without_authorizing_cleanup(
    tmp_path, monkeypatch, capsys,
):
    from cifereca.updater_retention_backups import write_dev_backup_receipt

    backups = tmp_path / "state" / "dev-updates"
    first = backups / ("a" * 16 + "-1700000000000000000")
    plugins = first / "plugins"
    plugins.mkdir(parents=True)
    first.chmod(0o700)
    write_dev_backup_receipt(first, snapshot_sha256="f" * 64)
    runtime = tmp_path / "runtime"
    root = tmp_path / "packages"
    monkeypatch.setattr(updater, "install_root", lambda: runtime)
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: root)
    monkeypatch.setattr(updater, "state_dir", lambda **_kwargs: tmp_path / "state")

    assert updater.main(["--retention-inspect", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "updater_backups" in report["coverage"]
    assert "updater_backups" in report["plans"]
    assert report["plans"]["updater_backups"]["total"] == 1
    assert "updater_backups" not in report["pending_stores"]
    assert "persistent_staging" in report["pending_stores"]
    assert report["safe_to_apply"] is False
    assert first.exists()



def test_retention_inspect_does_not_allocate_real_xdg_locations(
    tmp_path, monkeypatch, capsys,
):
    import os

    data_home = tmp_path / "xdg-data"
    state_home = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.delenv("CIFERECA_INSTALL_ROOT", raising=False)
    assert updater.main(["--retention-inspect", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["safe_to_apply"] is False
    assert not data_home.exists()
    assert not state_home.exists()
