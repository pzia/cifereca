"""#4708: aggregate historical missing evidence, never authorization."""
from __future__ import annotations

import json

from cifereca.core.missing_placements_cli import main
from cifereca.storage import Storage


def _setup(tmp_path):
    root = tmp_path / "books"
    for who in ("a", "b"):
        (root / who).mkdir(parents=True)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "library": {"directory": str(root)},
        "identities": [{"id": who, "label": who, "directory_name": who}
                       for who in ("a", "b")],
    }))
    database = tmp_path / "state.sqlite"
    store = Storage(database)
    ids = {}
    try:
        for who in ("a", "b"):
            store.identity_put({"identity_id": who, "label": who, "directory_name": who})
        for i, (name, who) in enumerate((
                ("returned.epub", "a"), ("linked.epub", "a"),
                ("unknown.epub", "a"), ("unlinked.epub", "a"),
                ("foreign.epub", "b"))):
            digest = f"{i + 1:064x}"
            aid = "sha256:" + digest
            store.artifact_put({"artifact_id": aid, "sha256": digest,
                                "size": 1, "media_format": "epub"})
            ids[name] = store.placement_put({
                "path": str(root / who / name), "identity_id": who,
                "artifact_id": aid, "provider_id": "local", "state": "missing",
                "source_item_id": name, "metadata": {},
            })
        store.catalog_entry_put({"catalog_entry_id": "local:linked",
                                 "placement_id": ids["linked.epub"],
                                 "artifact_id": "sha256:" + f"{2:064x}",
                                 "identity_id": "a", "provider_id": "local",
                                 "item_id": "linked.epub", "title": "secret-title"})
        store.metadata_set("core.synthetic-checkpoint", json.dumps({"undo": {"placement_id": ids["unknown.epub"]}}))
    finally:
        store.close()
    (root / "a" / "returned.epub").write_bytes(b"unverified")
    return database, config, root, ids


def _call(capsys, database, config):
    result = main(["--identity", "a", "--database", str(database),
                   "--config", str(config), "--historical"])
    raw = capsys.readouterr().out
    return result, json.loads(raw), raw


def test_historical_projection_is_private_scoped_and_read_only(tmp_path, capsys):
    db, config, root, ids = _setup(tmp_path)
    before = db.read_bytes(), config.read_bytes()
    code, report, raw = _call(capsys, db, config)
    assert code == 2
    assert report["historical"]["counts"] == {
        "returned_restore_candidate": 1, "historical_unlinked": 1,
        "historical_dependent": 1, "indeterminate": 1, "root_unavailable": 0,
    }
    assert report["historical"]["retirement_authorized"] is False
    assert report["historical"]["requires_fresh_confirmation"] is True
    assert all(v not in raw for v in ids.values())
    assert str(root) not in raw and "secret-title" not in raw
    assert "foreign.epub" not in raw
    assert (db.read_bytes(), config.read_bytes()) == before


def test_historical_offline_root_never_yields_unlinked(tmp_path, capsys):
    db, config, root, _ = _setup(tmp_path)
    (root / "a").rename(root / "offline")
    code, report, _ = _call(capsys, db, config)
    assert code == 2
    assert report["historical"]["counts"]["root_unavailable"] == 4
    assert report["historical"]["counts"]["historical_unlinked"] == 0
