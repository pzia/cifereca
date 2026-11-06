"""P0 #2847: installed CLI activation with real read-only SQLite and files."""
from __future__ import annotations

import json
from pathlib import Path

from cifereca.core.missing_placements_cli import main
from cifereca.storage import Storage


def _fixture(tmp_path):
    library = tmp_path / "EBooks"
    alice = library / "alice"
    bob = library / "bob"
    alice.mkdir(parents=True)
    bob.mkdir(parents=True)
    present = alice / "present.epub"
    present.write_bytes(b"synthetic in-scope bytes")
    foreign = bob / "foreign.epub"
    foreign.write_bytes(b"foreign synthetic bytes")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "library": {"directory": str(library)},
        "identities": [
            {"id": "alice", "label": "Alice", "directory_name": "alice"},
            {"id": "bob", "label": "Bob", "directory_name": "bob"},
        ],
    }), encoding="utf-8")
    config.chmod(0o644)  # Inspection must not call Config.load()/chmod.
    db = tmp_path / "state" / "library.sqlite3"
    store = Storage(db)
    try:
        for identity in ("alice", "bob"):
            store.identity_put({
                "identity_id": identity, "directory_name": identity,
                "label": identity,
            })
        digest = "a" * 64
        store.artifact_put({
            "artifact_id": "sha256:" + digest,
            "sha256": digest, "size": 25, "media_format": "epub",
        })
        for index, (owner, path, state) in enumerate([
            ("alice", present, "present"),
            ("alice", alice / "missing.epub", "missing"),
            ("bob", foreign, "present"),
        ]):
            store.placement_put({
                "path": str(path), "identity_id": owner,
                "artifact_id": "sha256:" + digest,
                "provider_id": "local", "source_item_id": f"fixture-{index}",
                "state": state, "metadata": {},
            })
    finally:
        store.close()
    return db, config, library, alice, bob, present, foreign


def _call(capsys, db, config, *, identity="alice"):
    code = main([
        "--identity", identity, "--config", str(config),
        "--database", str(db),
    ])
    raw = capsys.readouterr().out
    return code, json.loads(raw), raw


def test_installed_cli_counts_real_sqlite_without_any_source_mutation(
        tmp_path, capsys):
    db, config, library, alice, bob, present, foreign = _fixture(tmp_path)
    before = (db.read_bytes(), config.read_bytes(),
              config.stat().st_mode, present.read_bytes(),
              foreign.read_bytes())
    code, report, raw = _call(capsys, db, config)
    assert code == 0
    assert report["status"] == "complete"
    assert report["read_only"] is True
    assert report["repair_authorized"] is False
    assert report["acquisition_authorized"] is False
    assert report["root_count"] == 1
    assert report["counts"] == {
        "placements": 2, "verified_present": 1,
        "verified_missing": 1, "unavailable": 0,
        "out_of_scope": 0, "unexamined": 0,
    }
    assert report["missing_by_sqlite_state"]["missing"] == 1
    assert str(tmp_path) not in raw
    assert "alice" not in raw and "bob" not in raw
    assert "foreign" not in raw
    assert (db.read_bytes(), config.read_bytes(),
            config.stat().st_mode, present.read_bytes(),
            foreign.read_bytes()) == before


def test_unavailable_root_never_claims_zero_missing(tmp_path, capsys):
    db, config, library, alice, _bob, _present, _foreign = _fixture(tmp_path)
    alice.rename(library / "offline-alice")
    code, report, raw = _call(capsys, db, config)
    assert code == 2
    assert report["status"] == "incomplete_scan"
    assert report["counts"]["verified_missing"] == 0
    assert report["counts"]["unavailable"] == 2
    assert report["path_coverage"] == "incomplete"
    assert str(tmp_path) not in raw


def test_unexpected_identity_and_missing_database_fail_closed(
        tmp_path, capsys):
    db, config, _library, _alice, _bob, _present, _foreign = _fixture(tmp_path)
    for identity, database in (
        ("other", db), ("alice", tmp_path / "missing.sqlite3"),
    ):
        code, report, raw = _call(
            capsys, database, config, identity=identity)
        assert code == 2
        assert report["status"] == "incomplete_scan"
        assert report["counts"] is None
        assert report["repair_authorized"] is False
        assert str(tmp_path) not in raw


def test_config_is_not_modified_even_if_world_readable(tmp_path, capsys):
    db, config, *_ = _fixture(tmp_path)
    before = (config.read_bytes(), config.stat().st_mode)
    _call(capsys, db, config)
    assert (config.read_bytes(), config.stat().st_mode) == before


def test_cli_refuses_symlink_config_and_never_loads_foreign_credentials(
        tmp_path, capsys):
    db, config, *_ = _fixture(tmp_path)
    link = tmp_path / "linked-config.json"
    link.symlink_to(config)
    code, report, raw = _call(capsys, db, link)
    assert code == 2 and report["counts"] is None
    assert "inspection_unavailable" in report["reasons"]
    assert str(config) not in raw
