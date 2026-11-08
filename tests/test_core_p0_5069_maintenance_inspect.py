"""P0 #5069 B4: no writable/GC surface in maintenance CLI."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from cifereca import updater, updater_maintenance_inspect


NOW = datetime(2026, 11, 8, 20, tzinfo=timezone.utc).timestamp()


def _fixtures(tmp_path):
    runtime = tmp_path / "runtime"
    packages = tmp_path / "packages"
    for i in range(5):
        release = runtime / "releases" / f"v{i}"
        release.mkdir(parents=True)
        (release / ".cifereca-install.json").write_text(json.dumps({
            "installed_at": datetime.fromtimestamp(NOW - 200_000 - i * 100, timezone.utc).isoformat(),
            "version": "0.1.0",
        }), encoding="utf-8")
    folder = packages / "org.fake"
    for i in range(5):
        version = folder / f"1.0.{i}"
        version.mkdir(parents=True)
        (version / ".cifereca-install.json").write_text(json.dumps({
            "installed_at": datetime.fromtimestamp(NOW - 200_000 - i * 100, timezone.utc).isoformat(),
            "sha256": "a" * 64,
        }), encoding="utf-8")
        (version / "cifereca-plugin.json").write_text(json.dumps({
            "package": {"id": "org.fake", "version": f"1.0.{i}"},
        }), encoding="utf-8")
    return runtime, packages


def test_aggregate_is_private_read_only_and_marks_candidates_unverified(tmp_path):
    runtime, packages = _fixtures(tmp_path)
    files = sorted((str(p), p.stat().st_mtime_ns) for root in (runtime, packages)
                   for p in root.rglob("*"))
    report = updater_maintenance_inspect.inspect_maintenance(
        runtime_root=runtime, packages_root=packages, now=NOW,
    )
    assert report["ok"] is True and report["read_only"] is True
    assert report["safe_to_apply"] is False
    assert "cross_store_reference_graph_not_attested" in report["blockers"]
    by_store = {row["store"]: row for row in report["stores"]}
    assert by_store["release"]["observed"] == 5
    assert by_store["release"]["candidates_unverified"] == 3
    assert by_store["version"]["observed"] == 5
    assert by_store["version"]["candidates_unverified"] == 3
    assert "updater_backups" in report["pending_stores"]
    assert str(runtime) not in json.dumps(report)
    assert "org.fake" not in json.dumps(report)
    assert files == sorted((str(p), p.stat().st_mtime_ns) for root in (runtime, packages)
                           for p in root.rglob("*"))


def test_cli_json_stays_read_only_with_pending_stores(tmp_path, monkeypatch, capsys):
    runtime, packages = _fixtures(tmp_path)
    monkeypatch.setattr(updater, "install_root", lambda: runtime)
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: packages)
    assert updater_maintenance_inspect.main(["--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["safe_to_apply"] is False
    assert report["read_only"] is True
    assert captured.err == ""
    assert str(runtime) not in captured.out
    assert str(packages) not in captured.out


def test_apply_is_refused_even_if_stores_are_empty(tmp_path, monkeypatch, capsys):
    runtime, packages = _fixtures(tmp_path)
    monkeypatch.setattr(updater, "install_root", lambda: runtime)
    monkeypatch.setattr(updater, "_plugin_packages_root", lambda: packages)
    before = sorted(str(p) for p in runtime.rglob("*"))
    assert updater_maintenance_inspect.main(["--apply", "--json"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "ok": False, "safe_to_apply": False,
        "error": "maintenance_apply_not_available",
    }
    assert before == sorted(str(p) for p in runtime.rglob("*"))


def test_filesystem_exception_never_discloses_private_paths(monkeypatch, capsys):
    secret = "/private/user/tokens-NEVER-DISCLOSE"
    def failed(*_args, **_kwargs):
        raise RuntimeError(secret)
    monkeypatch.setattr(updater_maintenance_inspect, "inspect_maintenance", failed)
    assert updater_maintenance_inspect.main(["--json"]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert json.loads(captured.out)["error"] == "maintenance_inspect_unavailable"


def test_missing_and_symlinked_stores_do_not_expose_untrusted_data(tmp_path):
    root = tmp_path / "runtime"
    plugin_root = tmp_path / "plugins"
    private = tmp_path / "private"
    private.mkdir()
    (private / "SECRET").write_text("do not read", encoding="utf-8")
    plugin_root.symlink_to(private, target_is_directory=True)
    report = updater_maintenance_inspect.inspect_maintenance(
        runtime_root=root, packages_root=plugin_root, now=NOW,
    )
    assert report["safe_to_apply"] is False
    assert "plugins:unsafe_plugin_store" in report["blockers"]
    assert "private" not in json.dumps(report)
    assert "SECRET" not in json.dumps(report)


def test_cli_on_uninstalled_runtime_does_not_create_any_xdg_directory(
    tmp_path, monkeypatch, capsys,
):
    data_home = tmp_path / "data-not-created"
    runtime = tmp_path / "runtime-not-created"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("CIFERECA_INSTALL_ROOT", str(runtime))
    assert updater_maintenance_inspect.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["read_only"] is True
    assert not data_home.exists()
    assert not runtime.exists()
    assert list(tmp_path.iterdir()) == []
