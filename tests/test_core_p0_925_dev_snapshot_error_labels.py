"""P0 #925: untrusted snapshot paths, ZIP basenames and inventory labels stay private.

Tests use synthetic archives and preflight doubles only. No live plugin,
network, package activation or local identity is touched.
"""
from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace as NS

import pytest

from cifereca import dev_updater, updater
from cifereca.update_progress import update_operation


PRIVATE = "PRIVATE_READERS_TOKEN_2424"
SIGNED = "https://private.invalid/LOAN/book.acsm?token=" + PRIVATE


@pytest.mark.parametrize(("case", "expected"), [
    ("missing", "plugin absent ou hors snapshot"),
    ("sha", "SHA-256 plugin invalide"),
    ("duplicate", "package plugin dupliqué dans snapshot"),
])
def test_malformed_snapshot_manifest_never_reflects_private_path(
    tmp_path, monkeypatch, case, expected,
):
    content = tmp_path / "contents"
    content.mkdir()
    item = content / (PRIVATE + ".zip")
    item.write_bytes(b"synthetic-not-a-plugin")
    archive = tmp_path / "snapshot.zip"
    archive.write_bytes(b"synthetic-archive")
    rel = PRIVATE + ".zip"
    if case == "missing":
        item.unlink()
        items = [{"path": rel}]
    elif case == "sha":
        items = [{"path": rel, "sha256": "0" * 64}]
    else:
        items = [{"path": rel}, {"path": rel}]
    manifest = {"id": "synthetic", "core": "", "plugins": items}
    monkeypatch.setattr(dev_updater, "_extract_snapshot", lambda *_args: (content, manifest))
    monkeypatch.setattr(dev_updater, "_read_plugin_package_id",
                        lambda _path: ("org.demo", "1.0.0"))
    monkeypatch.setattr(dev_updater, "_package_is_active", lambda _id: None)
    monkeypatch.setattr(dev_updater, "_backup_plugins",
                        lambda *_args: pytest.fail("No backup before validation"))
    events, stream = [], io.StringIO()
    with pytest.raises(dev_updater.DevUpdateError) as failure:
        with update_operation("dev_snapshot", callback=events.append, stream=stream):
            dev_updater.apply_snapshot(archive, dry_run=True)
    assert expected in str(failure.value)
    assert PRIVATE not in str(failure.value) + stream.getvalue() + repr(events)
    assert events[-1]["outcome"] == "failed"


def test_plugin_zip_missing_manifest_does_not_expose_private_basename(tmp_path):
    archive = tmp_path / (PRIVATE + ".zip")
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("not-a-manifest.txt", PRIVATE)
    with pytest.raises(dev_updater.DevUpdateError) as failure:
        dev_updater._read_plugin_package_id(archive)
    assert str(failure.value) == "plugin ZIP invalide"
    assert PRIVATE not in str(failure.value)


def test_incomplete_plugin_manifest_does_not_echo_zip_filename(tmp_path):
    archive = tmp_path / (PRIVATE + ".zip")
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("cifereca-plugin.json", json.dumps({"package": {"id": "org.demo"}}))
    with pytest.raises(dev_updater.DevUpdateError) as failure:
        dev_updater._read_plugin_package_id(archive)
    assert str(failure.value) == "manifest plugin incomplet"
    assert PRIVATE not in str(failure.value)


def test_semantically_invalid_manifest_hides_plugin_filename_and_parser_text(
    tmp_path, monkeypatch,
):
    from cifereca.plugins import manifest as manifest_module
    archive = tmp_path / (PRIVATE + ".zip")
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("cifereca-plugin.json", json.dumps({
            "package": {"id": "org.demo", "version": "1.0.0"},
        }))
    def broken_manifest(_value):
        raise RuntimeError(SIGNED)
    monkeypatch.setattr(manifest_module, "parse_manifest", broken_manifest)
    with pytest.raises(dev_updater.DevUpdateError) as failure:
        dev_updater._read_plugin_package_id(archive)
    assert str(failure.value) == "manifest plugin invalide"
    assert PRIVATE not in str(failure.value)


def test_untrusted_smoke_contribution_id_does_not_enter_json_or_progress(
    monkeypatch, capsys,
):
    rows = [{"plugin_id": SIGNED, "package_id": "org.demo", "state": "error"}]
    monkeypatch.setattr(dev_updater, "_new_cli", lambda: "/offline/cli")
    monkeypatch.setattr(updater, "_run_bounded_process", lambda *_args, **_kw:
                        NS(returncode=0, stdout=json.dumps(rows), stderr=""))
    with pytest.raises(dev_updater.DevUpdateError) as failure:
        dev_updater._smoke_plugins(["org.demo"])
    assert str(failure.value) == "smoke plugins: contributions installées en erreur"
    assert PRIVATE not in str(failure.value)

    monkeypatch.setattr(dev_updater, "apply_snapshot", lambda *_args, **_kw:
                        (_ for _ in ()).throw(failure.value))
    assert dev_updater.main(["snapshot.zip", "--json"]) == 1
    printed = capsys.readouterr()
    assert json.loads(printed.out)["error"]["code"] == "dev_update_error"
    assert PRIVATE not in printed.out + printed.err
    assert printed.out.count('"ok"') == 1
